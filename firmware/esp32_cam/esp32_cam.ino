
/*
  SMARTMED ESP32 BRIDGE v5C
  Web -> ESP32 -> Nano -> Web

  Nano: firmware v5.0
  UART: 115200 baud
  ArduinoJson: 7.x

  ESP32-CAM:
    TX GPIO13 -> Nano RX D0
    RX GPIO14 <- Nano TX D1 qua chia ap

  ESP32 DevKit:
    TX GPIO17 -> Nano RX D0
    RX GPIO16 <- Nano TX D1 qua chia ap

  Quan trong:
    - Nano va ESP32 chung GND
    - Khong dua 5V vao chan RX cua ESP32
    - Khong dung camera/SD tren ESP32-CAM
*/

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <HardwareSerial.h>
#include <Preferences.h>
#include <time.h>
#include <math.h>

// =====================================
// 1. CAU HINH CUA BAN
// =====================================

// 1: ESP32-CAM AI Thinker
// 0: ESP32-D / ESP32 DevKit
#define BOARD_ESP32_CAM 1

const char* WIFI_SSID = "TEN_WIFI";
const char* WIFI_PASS = "MAT_KHAU_WIFI";

const char* SERVER_URL =
    "http://192.168.1.10:5000";

const char* DEVICE_CODE =
    "SMC-MA_THIET_BI";

const char* DEVICE_TOKEN =
    "TOKEN_BI_MAT";

// =====================================
// 2. KET NOI VA CHU KY
// =====================================

#if BOARD_ESP32_CAM
const int NANO_RX = 14;
const int NANO_TX = 13;
#else
const int NANO_RX = 16;
const int NANO_TX = 17;
#endif

HardwareSerial NanoUART(2);
Preferences prefs;

const uint32_t BAUD = 115200;
const uint8_t MAX_SCHEDULES = 8;
const uint8_t QUEUE_CAPACITY = 12;

const unsigned long HEARTBEAT_MS = 20000;
const unsigned long POLL_MS = 15000;
const unsigned long CLOCK_MS = 60000;
const unsigned long EVENT_RETRY_MS = 5000;
const unsigned long WIFI_RETRY_MS = 10000;
const unsigned long STATUS_MS = 120000;
const unsigned long UART_TIMEOUT_MS = 1800;

unsigned long lastHeartbeat = 0;
unsigned long lastPoll = 0;
unsigned long lastClock = 0;
unsigned long lastEventRetry = 0;
unsigned long lastWiFiRetry = 0;
unsigned long lastStatus = 0;

bool nanoStatusKnown = false;
uint32_t nanoRevision = 0;
uint32_t serverRevision = 0;

String serialLine;
String lastAck;
String lastError;

struct Snapshot {
    uint32_t revision;
    uint8_t count;
    uint32_t hash;
    String commands[MAX_SCHEDULES];
};

bool clockReady() {
    return time(nullptr) >= 1735689600;
}

bool parseUnsigned(
    const String& text,
    uint32_t& value
) {
    if (!text.length()) return false;

    for (size_t i = 0; i < text.length(); i++) {
        if (!isDigit(text[i])) return false;
    }

    char* end = nullptr;
    unsigned long n = strtoul(
        text.c_str(), &end, 10
    );

    if (*end != '\0') return false;

    value = (uint32_t)n;
    return true;
}

String eventKey(uint32_t number) {
    return "e" + String(
        number % QUEUE_CAPACITY
    );
}

// =====================================
// 3. HANG DOI SU KIEN FLASH
// =====================================

void initQueue() {
    if (!prefs.begin("smc5c", false)) {
        Serial.println(
            "[QUEUE] Khong mo duoc NVS."
        );
    }

    uint32_t head = prefs.getUInt(
        "head", 0
    );
    uint32_t tail = prefs.getUInt(
        "tail", 0
    );

    if (head > tail) {
        prefs.putUInt("head", tail);
    }
}

bool queueEvent(
    const String& eventType,
    uint32_t scheduleId,
    uint8_t compartment,
    uint32_t eventEpoch
) {
    uint32_t head = prefs.getUInt(
        "head", 0
    );
    uint32_t tail = prefs.getUInt(
        "tail", 0
    );

    if (tail - head >= QUEUE_CAPACITY) {
        Serial.println(
            "[QUEUE] DAY! Su kien moi "
            "khong duoc luu."
        );
        return false;
    }

    uint32_t sequence = prefs.getUInt(
        "seq", 0
    ) + 1;

    if (sequence == 0) {
        Serial.println(
            "[QUEUE] Sequence overflow."
        );
        return false;
    }

    if (!prefs.putUInt("seq", sequence)) {
        Serial.println(
            "[QUEUE] Loi luu sequence."
        );
        return false;
    }

    JsonDocument doc;
    doc["event_uid"] =
        String(DEVICE_CODE) + "-" + String(sequence);
    doc["schedule_id"] = scheduleId;
    doc["event_type"] = eventType;
    doc["compartment"] = compartment;

    if (eventEpoch > 1735689600) {
        doc["event_epoch"] = eventEpoch;
    } else {
        doc["event_epoch"] = nullptr;
    }

    String payload;
    serializeJson(doc, payload);

    String key = eventKey(tail);

    if (
        prefs.putString(
            key.c_str(), payload
        ) != payload.length()
    ) {
        Serial.println(
            "[QUEUE] Loi ghi su kien."
        );
        return false;
    }

    if (!prefs.putUInt("tail", tail + 1)) {
        Serial.println(
            "[QUEUE] Loi cap nhat tail."
        );
        return false;
    }

    Serial.print("[QUEUE] Luu: ");
    Serial.print(eventType);
    Serial.print(" | Schedule=");
    Serial.println(scheduleId);

    return true;
}

// =====================================
// 4. NHAN SU KIEN UART TU NANO
// =====================================

void parseNanoEvent(const String& line) {
    if (!line.startsWith("EVT:")) return;

    int p1 = line.indexOf(':', 4);
    if (p1 < 0) return;

    String eventType = line.substring(
        4, p1
    );

    if (
        eventType != "REMINDER" &&
        eventType != "TAKEN" &&
        eventType != "WRONG" &&
        eventType != "MISSED" &&
        eventType != "BUSY"
    ) {
        return;
    }

    int p2 = line.indexOf(':', p1 + 1);
    int p3 = p2 < 0
        ? -1 : line.indexOf(':', p2 + 1);

    if (p2 < 0 || p3 < 0) return;

    uint32_t id, slot, epoch;

    if (
        !parseUnsigned(
            line.substring(p1 + 1, p2), id
        ) ||
        !parseUnsigned(
            line.substring(p2 + 1, p3), slot
        ) ||
        !parseUnsigned(
            line.substring(p3 + 1), epoch
        ) ||
        id == 0 ||
        slot < 1 ||
        slot > 4
    ) {
        Serial.println(
            "[UART] Event khong hop le."
        );
        return;
    }

    queueEvent(
        eventType,
        id,
        (uint8_t)slot,
        epoch
    );
}

void handleNanoLine(String line) {
    line.trim();
    if (!line.length()) return;

    Serial.print("[NANO] ");
    Serial.println(line);

    if (line.startsWith("EVT:BOOT:")) {
        nanoStatusKnown = false;
    }

    if (line.startsWith("EVT:")) {
        parseNanoEvent(line);
    }

    if (line.startsWith("ACK:STATUS:")) {
        String payload = line.substring(11);
        int p = payload.indexOf(':');

        if (p >= 0) {
            uint32_t revision;

            if (parseUnsigned(
                payload.substring(0, p),
                revision
            )) {
                nanoRevision = revision;
                nanoStatusKnown = true;

                Serial.printf(
                    "[SYNC] Nano revision=%lu\n",
                    (unsigned long)nanoRevision
                );
            }
        }
    }

    if (line.startsWith("ACK:")) {
        lastAck = line;
    }

    if (line.startsWith("ERR:")) {
        lastError = line;
    }
}

void pollNano() {
    while (NanoUART.available()) {
        char c = (char)NanoUART.read();

        if (c == '\r') continue;

        if (c == '\n') {
            if (serialLine.length()) {
                handleNanoLine(serialLine);
            }
            serialLine = "";
            continue;
        }

        if (serialLine.length() < 220) {
            serialLine += c;
        } else {
            serialLine = "";
            Serial.println(
                "[UART] Dong qua dai."
            );
        }
    }
}

// =====================================
// 5. GUI LENH UART VA CHO ACK
// =====================================

bool sendWithAck(
    const String& command,
    const String& expected
) {
    for (uint8_t attempt = 0;
         attempt < 3; attempt++) {

        lastAck = "";
        lastError = "";

        NanoUART.println(command);

        Serial.print("[UART TX] ");
        Serial.println(command);

        unsigned long start = millis();

        while (
            millis() - start <
            UART_TIMEOUT_MS
        ) {
            pollNano();

            if (
                lastAck.startsWith(expected)
            ) {
                return true;
            }

            if (lastError.length()) {
                Serial.print("[UART] ");
                Serial.println(lastError);
                break;
            }

            delay(5);
        }

        Serial.printf(
            "[UART] Thu lai %u/3\n",
            attempt + 1
        );
    }

    Serial.println(
        "[UART] Khong nhan duoc ACK."
    );
    return false;
}

void requestNanoStatus() {
    if (
        sendWithAck(
            "CMD:STATUS", "ACK:STATUS:"
        )
    ) {
        lastStatus = millis();
    } else {
        nanoStatusKnown = false;
    }
}

// =====================================
// 6. WIFI VA DONG BO THOI GIAN
// =====================================

void connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    lastWiFiRetry = millis();

    Serial.println(
        "[WIFI] Dang ket noi..."
    );
}

void maintainWiFi() {
    static bool previousConnected = false;
    bool connected =
        WiFi.status() == WL_CONNECTED;

    if (connected && !previousConnected) {
        Serial.print("[WIFI] IP: ");
        Serial.println(WiFi.localIP());

        configTime(
            7 * 3600, 0,
            "pool.ntp.org",
            "time.google.com",
            "time.cloudflare.com"
        );

        Serial.println(
            "[NTP] Da khoi tao."
        );
    }

    if (!connected && previousConnected) {
        Serial.println("[WIFI] Mat WiFi.");
    }

    previousConnected = connected;

    if (
        !connected &&
        millis() - lastWiFiRetry >
            WIFI_RETRY_MS
    ) {
        WiFi.reconnect();
        lastWiFiRetry = millis();
    }
}

void syncNanoClock() {
    if (!clockReady()) return;

    uint32_t epoch = (uint32_t)time(
        nullptr
    );

    String command =
        "CMD:EPOCH:" + String(epoch);

    NanoUART.println(command);

    Serial.print("[CLOCK] ");
    Serial.println(command);
}

// =====================================
// 7. HTTP
// =====================================

bool beginHttp(
    HTTPClient& http,
    WiFiClient& client,
    const String& endpoint
) {
    String url = String(SERVER_URL)
        + endpoint;

    if (!http.begin(client, url)) {
        return false;
    }

    http.setConnectTimeout(3500);
    http.setTimeout(6000);

    http.addHeader(
        "X-Device-Code", DEVICE_CODE
    );
    http.addHeader(
        "X-Device-Token", DEVICE_TOKEN
    );
    http.addHeader(
        "Accept", "application/json"
    );

    return true;
}

void printHttpError(
    const char* name,
    int code
) {
    Serial.print("[HTTP] ");
    Serial.print(name);
    Serial.print(" code=");
    Serial.println(code);

    if (code == 401) {
        Serial.println(
            "[HTTP] Sai token thiet bi."
        );
    }
    if (code == 409) {
        Serial.println(
            "[HTTP] Du lieu lich co xung dot."
        );
    }
}

bool sendHeartbeat() {
    if (
        WiFi.status() != WL_CONNECTED
    ) return false;

    WiFiClient client;
    HTTPClient http;

    if (!beginHttp(
        http, client, "/api/iot/heartbeat"
    )) return false;

    http.addHeader(
        "Content-Type",
        "application/json"
    );

    JsonDocument doc;

    // Chi bao revision Nano DA AP DUNG,
    // khong bao revision ESP da tai.
    doc["installed_revision"] =
        nanoStatusKnown ? nanoRevision : 0;

    String payload;
    serializeJson(doc, payload);

    int code = http.POST(payload);
    String response = http.getString();
    http.end();

    if (code != 200) {
        printHttpError("heartbeat", code);
        return false;
    }

    JsonDocument result;

    if (
        deserializeJson(
            result, response
        )
    ) return false;

    if (!result["ok"].as<bool>()) {
        return false;
    }

    serverRevision = result[
        "current_revision"
    ].as<uint32_t>();

    Serial.printf(
        "[HB] OK | Nano=%lu Server=%lu | Synced=%s\n",
        (unsigned long)nanoRevision,
        (unsigned long)serverRevision,
        result["synchronized"].as<bool>()
            ? "YES" : "NO"
    );

    return true;
}

// =====================================
// 8. CHUYEN TEN THUOC VE ASCII OLED
// =====================================

String asciiMedicineName(
    const String& original,
    uint32_t scheduleId
) {
    String name = "";

    for (
        size_t i = 0;
        i < original.length()
            && name.length() < 13;
        i++
    ) {
        unsigned char c = original[i];

        if (
            c >= 32 &&
            c <= 126 &&
            c != ':'
        ) {
            name += (char)c;
        }
    }

    name.trim();

    if (!name.length()) {
        name = "THUOC "
            + String(scheduleId);
    }

    if (name.length() > 13) {
        name = name.substring(0, 13);
    }

    return name;
}

uint32_t dateKey(const String& text) {
    if (text.length() != 10) return 0;

    if (
        text[4] != '-' ||
        text[7] != '-'
    ) return 0;

    String digits = "";

    for (size_t i = 0; i < 10; i++) {
        if (i == 4 || i == 7) continue;

        if (!isDigit(text[i])) {
            return 0;
        }
        digits += text[i];
    }

    return (uint32_t)strtoul(
        digits.c_str(), nullptr, 10
    );
}

int minutesOfDay(const String& timeText) {
    if (
        timeText.length() != 5 ||
        timeText[2] != ':'
    ) return -1;

    if (
        !isDigit(timeText[0]) ||
        !isDigit(timeText[1]) ||
        !isDigit(timeText[3]) ||
        !isDigit(timeText[4])
    ) return -1;

    int hour = timeText.substring(
        0, 2
    ).toInt();

    int minute = timeText.substring(
        3, 5
    ).toInt();

    if (
        hour > 23 ||
        minute > 59
    ) return -1;

    return hour * 60 + minute;
}

// =====================================
// 9. HASH GIONG ARDUINO NANO V5.0
// =====================================

uint32_t fnvLine(const String& line) {
    uint32_t hash = 2166136261UL;

    for (size_t i = 0; i < line.length(); i++) {
        hash ^= (uint8_t)line[i];
        hash *= 16777619UL;
    }

    return hash;
}

bool parseSnapshot(
    JsonDocument& doc,
    Snapshot& snapshot
) {
    if (!doc["ok"].as<bool>()) {
        return false;
    }

    JsonArrayConst items =
        doc["schedules"].as<JsonArrayConst>();

    if (items.isNull()) return false;

    if (
        items.size() > MAX_SCHEDULES ||
        doc["schedule_count"].as<int>()
            != (int)items.size()
    ) {
        Serial.println(
            "[SYNC] Qua 8 lich hoac sai so luong."
        );
        return false;
    }

    snapshot.revision = doc[
        "revision"
    ].as<uint32_t>();

    snapshot.count = items.size();
    snapshot.hash = 2166136261UL;

    for (
        uint8_t i = 0;
        i < snapshot.count;
        i++
    ) {
        JsonVariantConst item = items[i];

        uint32_t id = item["id"]
            .as<uint32_t>();

        uint32_t slot = item[
            "compartment"
        ].as<uint32_t>();

        int minute = minutesOfDay(
            item["time"].as<String>()
        );

        uint32_t mask = item[
            "weekdays_mask"
        ].as<uint32_t>();

        uint32_t start = dateKey(
            item["start_date"].as<String>()
        );

        String endText =
            item["end_date"].as<String>();

        uint32_t end = endText.length()
            ? dateKey(endText) : 0;

        uint32_t grace = item[
            "grace_minutes"
        ].as<uint32_t>();

        String doseText = item[
            "dose"
        ]["quantity"].as<String>();

        float dose = doseText.toFloat();

        if (
            !isfinite(dose) ||
            dose <= 0 ||
            dose > 9999
        ) {
            Serial.println(
                "[SYNC] Lieu luong khong hop le."
            );
            return false;
        }

        uint32_t doseMilli = (
            uint32_t
        )lroundf(dose * 1000.0f);

        String name = asciiMedicineName(
            item["medicine"]["name"]
                .as<String>(),
            id
        );

        if (
            id == 0 ||
            slot < 1 || slot > 4 ||
            minute < 0 ||
            mask < 1 || mask > 127 ||
            start < 20200101 ||
            (endText.length() && end == 0) ||
            (end && end < start) ||
            grace < 1 || grace > 180 ||
            doseMilli == 0
        ) {
            Serial.println(
                "[SYNC] Du lieu lich khong hop le."
            );
            return false;
        }

        String command = "CFG:ITEM:";
        command += String(i) + ":";
        command += String(id) + ":";
        command += String(slot) + ":";
        command += String(minute) + ":";
        command += String(mask) + ":";
        command += String(start) + ":";
        command += String(end) + ":";
        command += String(grace) + ":";
        command += String(doseMilli) + ":";
        command += name;

        snapshot.commands[i] = command;

        uint32_t rawHash = fnvLine(
            command
        );

        snapshot.hash ^= rawHash;
        snapshot.hash *= 16777619UL;

        Serial.printf(
            "[SCHEDULE] ID=%lu Ngan=%lu Gio=%s Thuoc=%s\n",
            (unsigned long)id,
            (unsigned long)slot,
            item["time"].as<const char*>(),
            item["medicine"]["name"].as<const char*>()
        );
    }

    return true;
}

// =====================================
// 10. AP DUNG LICH XUONG NANO
// =====================================

bool applySnapshot(
    const Snapshot& snapshot
) {
    if (!nanoStatusKnown) {
        Serial.println(
            "[SYNC] Chua co trang thai Nano."
        );
        return false;
    }

    if (nanoRevision == snapshot.revision) {
        return true;
    }

    if (!clockReady()) {
        Serial.println(
            "[SYNC] Chua co gio NTP."
        );
        return false;
    }

    if (snapshot.revision == 0) {
        Serial.println(
            "[SYNC] Revision 0 khong dung "
            "de ghi EEPROM."
        );
        return false;
    }

    String beginCommand = "CFG:BEGIN:";
    beginCommand += String(
        snapshot.revision
    );
    beginCommand += ":";
    beginCommand += String(
        snapshot.count
    );

    String beginAck = "ACK:BEGIN:";
    beginAck += String(
        snapshot.revision
    );

    if (!sendWithAck(
        beginCommand, beginAck
    )) return false;

    for (
        uint8_t i = 0;
        i < snapshot.count;
        i++
    ) {
        String expected =
            "ACK:ITEM:" + String(i);

        if (!sendWithAck(
            snapshot.commands[i],
            expected
        )) {
            NanoUART.println("CFG:ABORT");
            return false;
        }
    }

    String commit = "CFG:COMMIT:";
    commit += String(snapshot.revision);
    commit += ":";
    commit += String(snapshot.hash);

    String commitAck = "ACK:APPLIED:";
    commitAck += String(
        snapshot.revision
    );

    if (!sendWithAck(commit, commitAck)) {
        // COMMIT co the da thanh cong nhung
        // mat ACK. Lan sau hoi STATUS lai.
        nanoStatusKnown = false;
        return false;
    }

    nanoRevision = snapshot.revision;
    nanoStatusKnown = true;

    Serial.printf(
        "[SYNC] APPLIED revision=%lu "
        "count=%u\n",
        (unsigned long)nanoRevision,
        snapshot.count
    );

    return true;
}

// =====================================
// 11. LAY LICH TU FLASK
// =====================================

bool fetchAndSyncSchedules() {
    if (
        WiFi.status() != WL_CONNECTED
    ) return false;

    WiFiClient client;
    HTTPClient http;

    if (!beginHttp(
        http, client, "/api/iot/schedules"
    )) return false;

    int code = http.GET();

    if (code != 200) {
        String body = http.getString();
        http.end();
        printHttpError("schedules", code);
        Serial.println(
            body.substring(0, 250)
        );
        return false;
    }

    int size = http.getSize();

    if (size > 16384) {
        http.end();
        Serial.println(
            "[SYNC] JSON qua lon."
        );
        return false;
    }

    String response = http.getString();
    http.end();

    if (
        response.length() == 0 ||
        response.length() > 16384
    ) return false;

    JsonDocument doc;
    if (deserializeJson(
        doc, response
    )) {
        Serial.println(
            "[SYNC] Loi JSON."
        );
        return false;
    }

    Snapshot snapshot;

    if (!parseSnapshot(
        doc, snapshot
    )) {
        return false;
    }

    serverRevision = snapshot.revision;

    Serial.printf(
        "[SYNC] WEB rev=%lu | NANO rev=%lu\n",
        (unsigned long)serverRevision,
        (unsigned long)nanoRevision
    );

    return applySnapshot(snapshot);
}

// =====================================
// 12. GUI CAC SU KIEN CON TON
// =====================================

void flushOneEvent() {
    if (
        WiFi.status() != WL_CONNECTED
    ) return;

    uint32_t head = prefs.getUInt(
        "head", 0
    );
    uint32_t tail = prefs.getUInt(
        "tail", 0
    );

    if (head >= tail) return;

    String key = eventKey(head);
    String payload = prefs.getString(
        key.c_str(), ""
    );

    if (!payload.length()) {
        prefs.putUInt("head", head + 1);
        return;
    }

    WiFiClient client;
    HTTPClient http;

    if (!beginHttp(
        http, client, "/api/iot/events"
    )) return;

    http.addHeader(
        "Content-Type",
        "application/json"
    );

    int code = http.POST(payload);
    String response = http.getString();
    http.end();

    if (code != 200) {
        printHttpError("events", code);
        return;
    }

    JsonDocument doc;

    if (
        deserializeJson(
            doc, response
        ) ||
        !doc["ok"].as<bool>() ||
        !doc["accepted"].as<bool>()
    ) {
        return;
    }

    // Chi xoa sau khi Flask xac nhan.
    prefs.remove(key.c_str());
    prefs.putUInt("head", head + 1);

    Serial.println(
        "[EVENT] WEB ACK - Da luu su kien."
    );
}

// =====================================
// 13. SETUP VA LOOP
// =====================================

void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println();
    Serial.println(
        "SMARTMED ESP32 BRIDGE v5C"
    );

    NanoUART.begin(
        BAUD,
        SERIAL_8N1,
        NANO_RX,
        NANO_TX
    );

    Serial.printf(
        "[UART] RX=%d TX=%d\n",
        NANO_RX,
        NANO_TX
    );

    initQueue();
    connectWiFi();

    delay(700);
    requestNanoStatus();
}

void loop() {
    pollNano();
    maintainWiFi();

    unsigned long now = millis();

    if (
        !nanoStatusKnown &&
        now - lastStatus > 5000
    ) {
        lastStatus = now;
        requestNanoStatus();
    }

    if (
        nanoStatusKnown &&
        now - lastStatus > STATUS_MS
    ) {
        requestNanoStatus();
    }

    if (
        clockReady() &&
        (
            lastClock == 0 ||
            now - lastClock >= CLOCK_MS
        )
    ) {
        lastClock = now;
        syncNanoClock();
    }

    if (
        WiFi.status() == WL_CONNECTED
    ) {
        if (
            lastHeartbeat == 0 ||
            now - lastHeartbeat >=
                HEARTBEAT_MS
        ) {
            lastHeartbeat = now;
            sendHeartbeat();
        }

        if (
            nanoStatusKnown &&
            (
                lastPoll == 0 ||
                now - lastPoll >= POLL_MS
            )
        ) {
            lastPoll = now;
            fetchAndSyncSchedules();
        }

        if (
            lastEventRetry == 0 ||
            now - lastEventRetry >=
                EVENT_RETRY_MS
        ) {
            lastEventRetry = now;
            flushOneEvent();
        }
    }

    delay(10);
}
