
/*
  SMART MEDICINE CABINET - NANO V5.0
  5C.1 - UART DYNAMIC SCHEDULE

  HARDWARE:
  D3      BUZZER
  D4-D7   LED NGAN 1..4
  D8      LED CANH BAO
  D9-D12  IR NGAN 1..4 (ACTIVE LOW)
  A4/A5   OLED SSD1306 I2C 0x3C
  D0/D1   UART ESP32, 115200

  Khong con lich demo 07,12,19,23.
  Khong tu nhac neu chua co gio hop le.
  Toi da 8 lich.
*/

#include <Arduino.h>
#include <Wire.h>
#include <U8x8lib.h>
#include <EEPROM.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stddef.h>

#define MAX_SCHEDULES 8
#define IR_ACTIVE_LEVEL LOW
#define VN_OFFSET 25200UL

const uint8_t PIN_BUZZER = 3;
const uint8_t PIN_LEDS[4] = {4, 5, 6, 7};
const uint8_t PIN_RED = 8;
const uint8_t PIN_IR[4] = {9, 10, 11, 12};

const uint32_t EEPROM_MAGIC = 0x534D4335UL;
const uint8_t EEPROM_VALID = 0xA5;

const uint32_t DEBOUNCE_MS = 50;
const uint32_t WRONG_MS = 3500;
const uint32_t RESULT_MS = 5000;
const uint32_t CLOCK_MAX_AGE_MS = 86400000UL;

U8X8_SSD1306_128X64_NONAME_HW_I2C oled(
    U8X8_PIN_NONE
);

struct __attribute__((packed)) Schedule {
    uint32_t id;
    uint32_t startDate;
    uint32_t endDate;
    uint32_t doseMilli;
    uint16_t minuteOfDay;
    uint16_t graceMinutes;
    uint8_t weekdaysMask;
    uint8_t compartment;
    char name[14];
};

struct __attribute__((packed)) ScheduleBank {
    uint32_t magic;
    uint8_t valid;
    uint8_t count;
    uint16_t checksum;
    uint32_t revision;
    Schedule items[MAX_SCHEDULES];
};

Schedule activeSchedules[MAX_SCHEDULES];
Schedule stagedSchedules[MAX_SCHEDULES];
Schedule currentSchedule;

uint8_t activeCount = 0;
uint32_t activeRevision = 0;
int8_t activeBank = -1;

bool staging = false;
uint32_t stagedRevision = 0;
uint8_t stagedCount = 0;
uint8_t stagedReceived = 0;
uint32_t stagedHash = 2166136261UL;
uint32_t lastItemHash = 0;

bool clockValid = false;
uint32_t baseEpochUTC = 0;
uint32_t baseMillis = 0;

uint32_t lastFiredMinute[MAX_SCHEDULES];
uint32_t lastClockCheck = 0;
uint32_t lastDisplayUpdate = 0;

enum RunState {
    READY,
    REMINDING,
    MISSED,
    TAKEN
};

RunState state = READY;
bool testSession = false;
uint32_t reminderStarted = 0;
uint32_t resultStarted = 0;
uint32_t wrongUntil = 0;

uint8_t lastRaw[4];
uint8_t stableIR[4];
uint32_t lastIRChange[4];

char uartLine[120];
uint8_t uartLength = 0;

uint16_t crc16(
    const uint8_t* data,
    size_t length,
    uint16_t crc
) {
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t b = 0; b < 8; b++) {
            crc = (crc & 0x8000)
                ? (crc << 1) ^ 0x1021
                : crc << 1;
        }
    }
    return crc;
}

uint16_t bankChecksum(const ScheduleBank& bank) {
    uint16_t crc = crc16(
        &bank.count, 1, 0xFFFF
    );
    return crc16(
        (const uint8_t*)&bank.revision,
        sizeof(bank.revision) + sizeof(bank.items),
        crc
    );
}

bool bankIsValid(const ScheduleBank& bank) {
    return (
        bank.magic == EEPROM_MAGIC &&
        bank.valid == EEPROM_VALID &&
        bank.count <= MAX_SCHEDULES &&
        bank.checksum == bankChecksum(bank)
    );
}

void loadSchedules() {
    if (
        sizeof(ScheduleBank) * 2 >
        (size_t)EEPROM.length()
    ) {
        Serial.println(F("ERR:EEPROM_TOO_SMALL"));
        return;
    }

    ScheduleBank bank;

    for (uint8_t index = 0; index < 2; index++) {
        EEPROM.get(
            index * sizeof(ScheduleBank),
            bank
        );

        if (!bankIsValid(bank)) continue;

        if (
            activeBank == -1 ||
            (int32_t)(
                bank.revision - activeRevision
            ) > 0
        ) {
            activeBank = index;
            activeRevision = bank.revision;
            activeCount = bank.count;
            memcpy(
                activeSchedules,
                bank.items,
                sizeof(activeSchedules)
            );
        }
    }

    Serial.print(F("EVT:BOOT:"));
    Serial.print(activeRevision);
    Serial.print(':');
    Serial.println(activeCount);
}

bool saveSchedules() {
    if (
        sizeof(ScheduleBank) * 2 >
        (size_t)EEPROM.length()
    ) return false;

    ScheduleBank bank;
    memset(&bank, 0, sizeof(bank));

    bank.magic = EEPROM_MAGIC;
    bank.valid = 0;
    bank.count = stagedCount;
    bank.revision = stagedRevision;

    memcpy(
        bank.items,
        stagedSchedules,
        sizeof(stagedSchedules)
    );

    bank.checksum = bankChecksum(bank);

    uint8_t target = activeBank == 0 ? 1 : 0;
    int address = target * sizeof(ScheduleBank);
    int validAddress = address
        + offsetof(ScheduleBank, valid);

    // Danh dau bank moi chua hop le.
    EEPROM.update(validAddress, 0);

    // Ghi du lieu, sau do moi bat co valid.
    EEPROM.put(address, bank);
    EEPROM.update(validAddress, EEPROM_VALID);

    ScheduleBank verify;
    EEPROM.get(address, verify);

    if (!bankIsValid(verify)) return false;

    memcpy(
        activeSchedules,
        stagedSchedules,
        sizeof(activeSchedules)
    );

    activeCount = stagedCount;
    activeRevision = stagedRevision;
    activeBank = target;

    for (uint8_t i = 0; i < MAX_SCHEDULES; i++) {
        lastFiredMinute[i] = 0;
    }

    return true;
}

uint32_t fnv1a(
    uint32_t hash,
    const char* text
) {
    while (*text) {
        hash ^= (uint8_t)*text++;
        hash *= 16777619UL;
    }
    return hash;
}

bool parseNumber(
    const char* text,
    uint32_t* output
) {
    if (!text || !*text) return false;

    for (const char* p = text; *p; p++) {
        if (*p < '0' || *p > '9') return false;
    }

    char* end = NULL;
    unsigned long number = strtoul(
        text, &end, 10
    );

    if (*end != '\0') return false;

    *output = (uint32_t)number;
    return true;
}

void sendError(const char* reason) {
    Serial.print(F("ERR:"));
    Serial.println(reason);
}

bool hasValidClock() {
    return clockValid &&
        (uint32_t)(millis() - baseMillis)
            < CLOCK_MAX_AGE_MS;
}

uint32_t nowUTC() {
    return baseEpochUTC
        + (uint32_t)(
            (millis() - baseMillis) / 1000UL
        );
}

uint32_t localEpoch() {
    return nowUTC() + VN_OFFSET;
}

uint32_t dateFromDays(uint32_t days) {
    int32_t z = (int32_t)days + 719468;
    int32_t era = z / 146097;
    uint32_t doe = z - era * 146097;
    uint32_t yoe = (
        doe - doe / 1460 +
        doe / 36524 - doe / 146096
    ) / 365;

    int32_t year = (int32_t)yoe + era * 400;
    uint32_t doy = doe - (
        365 * yoe + yoe / 4 - yoe / 100
    );
    uint32_t mp = (5 * doy + 2) / 153;
    uint32_t day = doy
        - (153 * mp + 2) / 5 + 1;
    uint32_t month = mp
        + (mp < 10 ? 3 : -9);

    year += month <= 2;

    return (uint32_t)year * 10000UL
        + month * 100UL + day;
}

void sendDoseEvent(
    const char* type,
    uint8_t actualSlot
) {
    Serial.print(F("EVT:"));
    Serial.print(type);
    Serial.print(':');
    Serial.print(currentSchedule.id);
    Serial.print(':');
    Serial.print(actualSlot);
    Serial.print(':');
    Serial.println(
        hasValidClock() ? nowUTC() : 0
    );
}

void startReminder(
    const Schedule& schedule,
    bool isTest
) {
    if (state == REMINDING) return;

    currentSchedule = schedule;
    testSession = isTest;
    state = REMINDING;
    reminderStarted = millis();
    wrongUntil = 0;

    if (testSession) {
        Serial.println(F("EVT:TEST:BEGIN"));
    } else {
        sendDoseEvent(
            "REMINDER",
            currentSchedule.compartment
        );
    }
}

void finishReminder(bool isMissed) {
    state = isMissed ? MISSED : TAKEN;
    resultStarted = millis();

    if (testSession) {
        Serial.println(
            isMissed
                ? F("EVT:TEST:MISSED")
                : F("EVT:TEST:TAKEN")
        );
    } else {
        sendDoseEvent(
            isMissed ? "MISSED" : "TAKEN",
            currentSchedule.compartment
        );
    }
}

void onIR(uint8_t slot) {
    if (state != REMINDING) return;

    if (slot == currentSchedule.compartment) {
        finishReminder(false);
    } else {
        wrongUntil = millis() + WRONG_MS;

        if (testSession) {
            Serial.print(F("EVT:TEST:WRONG:"));
            Serial.println(slot);
        } else {
            sendDoseEvent("WRONG", slot);
        }
    }
}

void pollIR() {
    uint32_t now = millis();

    for (uint8_t i = 0; i < 4; i++) {
        uint8_t raw = digitalRead(PIN_IR[i]);

        if (raw != lastRaw[i]) {
            lastRaw[i] = raw;
            lastIRChange[i] = now;
        }

        if (
            raw != stableIR[i] &&
            (uint32_t)(
                now - lastIRChange[i]
            ) >= DEBOUNCE_MS
        ) {
            stableIR[i] = raw;

            if (raw == IR_ACTIVE_LEVEL) {
                onIR(i + 1);
            }
        }
    }
}

void checkSchedules() {
    if (!hasValidClock()) return;

    uint32_t local = localEpoch();
    uint32_t localMinute = local / 60UL;

    static uint32_t previousMinute = 0;

    if (localMinute == previousMinute) return;
    previousMinute = localMinute;

    uint32_t days = local / 86400UL;
    uint32_t dateKey = dateFromDays(days);
    uint16_t minuteOfDay =
        (local % 86400UL) / 60UL;

    // 1970-01-01 la Thu Nam.
    // Thu Hai=bit 0; Chu Nhat=bit 6.
    uint8_t weekday = (days + 3) % 7;

    for (uint8_t i = 0; i < activeCount; i++) {
        const Schedule& s = activeSchedules[i];

        if (
            minuteOfDay != s.minuteOfDay ||
            !(s.weekdaysMask & (1U << weekday)) ||
            dateKey < s.startDate ||
            (s.endDate && dateKey > s.endDate) ||
            lastFiredMinute[i] == localMinute
        ) continue;

        lastFiredMinute[i] = localMinute;

        if (state == REMINDING) {
            // Khong ghi de loi nhac dang dien ra.
            // Lich bi trung thoi gian xu ly sau.
            currentSchedule = s;
            sendDoseEvent("BUSY", s.compartment);
            continue;
        }

        if (state == MISSED || state == TAKEN) {
            state = READY;
        }

        startReminder(s, false);
    }
}

void updateOutputs() {
    uint32_t now = millis();

    bool blink = (now / 400UL) % 2 == 0;

    for (uint8_t i = 0; i < 4; i++) {
        bool on = state == REMINDING &&
            currentSchedule.compartment == i + 1
            && blink;

        digitalWrite(
            PIN_LEDS[i],
            on ? HIGH : LOW
        );
    }

    bool wrongVisible =
        (int32_t)(wrongUntil - now) > 0;

    digitalWrite(
        PIN_RED,
        wrongVisible || state == MISSED
            ? HIGH : LOW
    );

    bool reminderBeep =
        state == REMINDING &&
        (uint32_t)(
            now - reminderStarted
        ) < 10000UL &&
        (now % 700UL) < 190UL;

    bool wrongBeep =
        wrongVisible &&
        (now % 400UL) < 100UL;

    // Giu kieu buzzer ON/OFF qua transistor
    // nhu firmware Nano cu.
    digitalWrite(
        PIN_BUZZER,
        reminderBeep || wrongBeep
            ? HIGH : LOW
    );
}

void checkReminderTimeout() {
    uint32_t now = millis();

    if (
        state == REMINDING &&
        (uint32_t)(
            now - reminderStarted
        ) >=
        (uint32_t)currentSchedule.graceMinutes
            * 60000UL
    ) {
        finishReminder(true);
    }

    if (
        (state == TAKEN || state == MISSED) &&
        (uint32_t)(
            now - resultStarted
        ) >= RESULT_MS
    ) {
        state = READY;
    }
}

void oledLine(
    uint8_t row,
    const char* content
) {
    char buffer[17];
    memset(buffer, ' ', 16);
    buffer[16] = '\0';

    size_t length = strlen(content);
    if (length > 16) length = 16;

    memcpy(buffer, content, length);
    oled.drawString(0, row, buffer);
}

void updateOLED() {
    if (
        millis() - lastDisplayUpdate < 1000UL
    ) return;

    lastDisplayUpdate = millis();

    char line[17];

    oledLine(0, "SMARTMED NANO V5");

    if (!hasValidClock()) {
        oledLine(1, "CHO GIO TU ESP");
        oledLine(2, "KHONG NHAC THUOC");
    } else {
        uint32_t local = localEpoch();
        uint32_t secondOfDay =
            local % 86400UL;

        snprintf(
            line, sizeof(line),
            "%02u:%02u:%02u",
            (unsigned)(secondOfDay / 3600UL),
            (unsigned)(
                (secondOfDay / 60UL) % 60
            ),
            (unsigned)(secondOfDay % 60)
        );
        oledLine(1, line);

        uint32_t dateKey = dateFromDays(
            local / 86400UL
        );

        snprintf(
            line, sizeof(line),
            "DATE:%08lu",
            (unsigned long)dateKey
        );
        oledLine(2, line);
    }

    snprintf(
        line, sizeof(line),
        "REV:%lu N:%u",
        (unsigned long)activeRevision,
        activeCount
    );
    oledLine(3, line);

    if (state == REMINDING) {
        oledLine(4, "NHAC LAY THUOC");

        snprintf(
            line, sizeof(line),
            "NGAN:%u",
            currentSchedule.compartment
        );
        oledLine(5, line);
        oledLine(6, currentSchedule.name);
        oledLine(7, "CHO CAM BIEN IR");
    } else if (state == TAKEN) {
        oledLine(4, "DA PHAT HIEN LAY");
        oledLine(5, currentSchedule.name);
        oledLine(6, " ");
        oledLine(7, " ");
    } else if (state == MISSED) {
        oledLine(4, "BO LO LAN NHAC");
        oledLine(5, currentSchedule.name);
        oledLine(6, " ");
        oledLine(7, " ");
    } else {
        oledLine(4, "SAN SANG");
        oledLine(5, "LICH TU WEB");
        oledLine(6, " ");
        oledLine(7, " ");
    }
}

void sendStatus() {
    Serial.print(F("ACK:STATUS:"));
    Serial.print(activeRevision);
    Serial.print(':');
    Serial.print(activeCount);
    Serial.print(':');
    Serial.println(hasValidClock() ? 1 : 0);
}

void processItem(char* line, uint32_t rawHash) {
    if (!staging) {
        sendError("NO_BEGIN");
        return;
    }

    char* saveptr = NULL;
    char* fields[10];

    // Bo qua "CFG:ITEM:"
    char* cursor = line + 9;

    for (uint8_t i = 0; i < 10; i++) {
        fields[i] = strtok_r(
            i == 0 ? cursor : NULL,
            ":", &saveptr
        );

        if (!fields[i]) {
            sendError("BAD_ITEM");
            return;
        }
    }

    uint32_t values[9];

    for (uint8_t i = 0; i < 9; i++) {
        if (!parseNumber(fields[i], &values[i])) {
            sendError("BAD_NUMBER");
            return;
        }
    }

    uint32_t index = values[0];

    if (
        index + 1 == stagedReceived &&
        rawHash == lastItemHash
    ) {
        Serial.print(F("ACK:ITEM:"));
        Serial.println(index);
        return;
    }

    if (
        index != stagedReceived ||
        index >= stagedCount
    ) {
        sendError("ITEM_ORDER");
        return;
    }

    Schedule s;
    memset(&s, 0, sizeof(s));

    s.id = values[1];
    s.compartment = values[2];
    s.minuteOfDay = values[3];
    s.weekdaysMask = values[4];
    s.startDate = values[5];
    s.endDate = values[6];
    s.graceMinutes = values[7];
    s.doseMilli = values[8];

    if (
        s.id == 0 ||
        s.compartment < 1 ||
        s.compartment > 4 ||
        s.minuteOfDay >= 1440 ||
        s.weekdaysMask < 1 ||
        s.weekdaysMask > 127 ||
        s.startDate < 20200101UL ||
        (s.endDate &&
         s.endDate < s.startDate) ||
        s.graceMinutes < 1 ||
        s.graceMinutes > 180 ||
        s.doseMilli == 0 ||
        s.doseMilli > 9999000UL
    ) {
        sendError("ITEM_RANGE");
        return;
    }

    // Ten ASCII, toi da 13 ky tu,
    // de hien thi tren OLED U8x8.
    for (uint8_t i = 0; fields[9][i]; i++) {
        uint8_t ch = (uint8_t)fields[9][i];
        if (ch < 32 || ch > 126) {
            sendError("NAME_ASCII");
            return;
        }
    }

    strncpy(
        s.name, fields[9],
        sizeof(s.name) - 1
    );

    stagedSchedules[stagedReceived] = s;
    stagedReceived++;
    lastItemHash = rawHash;

    Serial.print(F("ACK:ITEM:"));
    Serial.println(index);
}

void processLine(char* line) {
    if (strcmp(line, "CMD:PING") == 0) {
        Serial.println(F("EVT:ACK:PING"));
        return;
    }

    if (strcmp(line, "CMD:STATUS") == 0) {
        sendStatus();
        return;
    }

    if (strncmp(line, "CMD:EPOCH:", 10) == 0) {
        uint32_t epoch;

        if (
            !parseNumber(line + 10, &epoch) ||
            epoch < 1735689600UL
        ) {
            sendError("BAD_EPOCH");
            return;
        }

        baseEpochUTC = epoch;
        baseMillis = millis();
        clockValid = true;

        Serial.print(F("ACK:EPOCH:"));
        Serial.println(epoch);
        return;
    }

    if (strncmp(line, "CMD:TEST:", 9) == 0) {
        uint32_t slot;

        if (
            !parseNumber(line + 9, &slot) ||
            slot < 1 || slot > 4
        ) {
            sendError("BAD_TEST_SLOT");
            return;
        }

        Schedule test;
        memset(&test, 0, sizeof(test));
        test.id = 0;
        test.compartment = slot;
        test.graceMinutes = 1;
        strcpy(test.name, "TEST CAM BIEN");

        startReminder(test, true);
        return;
    }

    if (strncmp(line, "CFG:BEGIN:", 10) == 0) {
        char* separator = strchr(
            line + 10, ':'
        );

        if (!separator) {
            sendError("BAD_BEGIN");
            return;
        }

        *separator = '\0';

        uint32_t revision;
        uint32_t count;

        if (
            !parseNumber(
                line + 10, &revision
            ) ||
            !parseNumber(
                separator + 1, &count
            ) ||
            revision == 0 ||
            count > MAX_SCHEDULES
        ) {
            sendError("BAD_BEGIN");
            return;
        }

        staging = true;
        stagedRevision = revision;
        stagedCount = count;
        stagedReceived = 0;
        stagedHash = 2166136261UL;
        lastItemHash = 0;

        memset(
            stagedSchedules, 0,
            sizeof(stagedSchedules)
        );

        Serial.print(F("ACK:BEGIN:"));
        Serial.println(revision);
        return;
    }

    if (strncmp(line, "CFG:ITEM:", 9) == 0) {
        uint32_t rawHash = fnv1a(
            2166136261UL, line
        );

        uint8_t before = stagedReceived;
        processItem(line, rawHash);

        if (stagedReceived > before) {
            // Hash noi tiep tren toan bo lenh ITEM.
            // ESP32 phai tinh cung cach.
            // Hash rawHash duoc tinh rieng cho retry.
            // Hash staged se do ESP32 gui qua COMMIT.
            stagedHash ^= rawHash;
            stagedHash *= 16777619UL;
        }
        return;
    }

    if (strncmp(line, "CFG:COMMIT:", 11) == 0) {
        char* separator = strchr(
            line + 11, ':'
        );

        if (!separator) {
            sendError("BAD_COMMIT");
            return;
        }

        *separator = '\0';

        uint32_t revision;
        uint32_t hash;

        if (
            !parseNumber(
                line + 11, &revision
            ) ||
            !parseNumber(
                separator + 1, &hash
            )
        ) {
            sendError("BAD_COMMIT");
            return;
        }

        // Neu ACK COMMIT lan truoc bi mat,
        // cho phep gui lai.
        if (
            !staging &&
            revision == activeRevision
        ) {
            Serial.print(F("ACK:APPLIED:"));
            Serial.println(revision);
            return;
        }

        if (
            !staging ||
            revision != stagedRevision ||
            stagedReceived != stagedCount ||
            hash != stagedHash
        ) {
            sendError("COMMIT_MISMATCH");
            return;
        }

        if (!saveSchedules()) {
            sendError("EEPROM_WRITE");
            return;
        }

        staging = false;

        Serial.print(F("ACK:APPLIED:"));
        Serial.println(activeRevision);
        return;
    }

    if (strcmp(line, "CFG:ABORT") == 0) {
        staging = false;
        Serial.println(F("ACK:ABORT"));
        return;
    }

    sendError("UNKNOWN_COMMAND");
}

void readUART() {
    while (Serial.available()) {
        char c = (char)Serial.read();

        if (c == '\r') continue;

        if (c == '\n') {
            if (uartLength) {
                uartLine[uartLength] = '\0';
                processLine(uartLine);
            }

            uartLength = 0;
            continue;
        }

        if (uartLength < sizeof(uartLine) - 1) {
            uartLine[uartLength++] = c;
        } else {
            uartLength = 0;
            sendError("LINE_TOO_LONG");
        }
    }
}

void setup() {
    Serial.begin(115200);

    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_RED, OUTPUT);

    digitalWrite(PIN_BUZZER, LOW);
    digitalWrite(PIN_RED, LOW);

    for (uint8_t i = 0; i < 4; i++) {
        pinMode(PIN_LEDS[i], OUTPUT);
        digitalWrite(PIN_LEDS[i], LOW);

        pinMode(PIN_IR[i], INPUT_PULLUP);
        lastRaw[i] = digitalRead(PIN_IR[i]);
        stableIR[i] = lastRaw[i];
        lastIRChange[i] = millis();
    }

    memset(
        lastFiredMinute, 0,
        sizeof(lastFiredMinute)
    );

    oled.setI2CAddress(0x3C << 1);
    oled.begin();
    oled.setFont(
        u8x8_font_chroma48medium8_r
    );
    oled.clear();

    loadSchedules();

    Serial.println(F("NANO V5.0 READY"));
    sendStatus();
}

void loop() {
    readUART();
    pollIR();
    checkSchedules();
    checkReminderTimeout();
    updateOutputs();
    updateOLED();
}
