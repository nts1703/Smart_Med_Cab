
import hashlib
import hmac
import re
from datetime import datetime, timezone, timedelta
from flask import (
    Blueprint, request, jsonify, g, current_app
)
from services.database import (
    fetch_one, get_connection, execute_query
)

iot_bp = Blueprint(
    "iot", __name__, url_prefix="/api/iot"
)

MAX_NANO_SCHEDULES = 4
EVENT_TYPES = {
    "REMINDER", "TAKEN", "WRONG",
    "MISSED", "BUSY"
}

def error_response(code, message, status=400):
    return jsonify({
        "ok": False,
        "error": code,
        "message": message
    }), status

def fmt_time(value):
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        return f"{total // 3600:02d}:{total // 60 % 60:02d}"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]

@iot_bp.before_request
def authenticate_device():
    code = request.headers.get(
        "X-Device-Code", ""
    ).strip()
    token = request.headers.get(
        "X-Device-Token", ""
    ).strip()

    if (
        not code or not token
        or len(code) > 100
        or len(token) > 256
    ):
        return error_response(
            "AUTH_REQUIRED",
            "Thiếu thông tin xác thực thiết bị.",
            401
        )

    try:
        device = fetch_one("""
            SELECT id, device_code, device_name,
                   owner_user_id, api_token_hash,
                   operational_status,
                   schedule_revision, timezone
            FROM devices
            WHERE device_code = %s
            LIMIT 1
        """, (code,))
    except Exception:
        current_app.logger.exception(
            "Khong truy van duoc thiet bi"
        )
        return error_response(
            "DATABASE_ERROR",
            "Không kiểm tra được thiết bị.",
            503
        )

    digest = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    saved_digest = (
        str(device["api_token_hash"]).lower()
        if device and device["api_token_hash"]
        else ""
    )

    if (
        not device
        or not hmac.compare_digest(
            digest, saved_digest
        )
    ):
        return error_response(
            "AUTH_FAILED",
            "Mã thiết bị hoặc token không hợp lệ.",
            401
        )

    if device["operational_status"] != "active":
        return error_response(
            "DEVICE_DISABLED",
            "Thiết bị đã bị tạm ngừng.",
            403
        )

    g.iot_device = device

@iot_bp.get("/schedules")
def schedules():
    device_id = g.iot_device["id"]
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, device_code, device_name,
                       owner_user_id, operational_status,
                       schedule_revision, timezone
                FROM devices
                WHERE id = %s
                FOR UPDATE
            """, (device_id,))
            device = cursor.fetchone()

            if (
                not device
                or device["operational_status"] != "active"
            ):
                conn.rollback()
                return error_response(
                    "DEVICE_DISABLED",
                    "Thiết bị không hoạt động.",
                    403
                )

            cursor.execute("""
                SELECT
                    s.id,
                    s.user_medicine_id,
                    s.compartment_number,
                    s.dose_quantity,
                    s.dose_unit,
                    s.reminder_time,
                    s.weekdays_mask,
                    s.start_date,
                    s.end_date,
                    s.grace_minutes,
                    s.instructions,
                    s.revision,
                    m.medicine_name,
                    m.strength,
                    m.dosage_form,
                    m.user_id AS medicine_owner,
                    m.is_active AS medicine_active,
                    c.user_medicine_id AS assigned_id,
                    c.is_enabled AS slot_enabled
                FROM medicine_schedules s
                JOIN user_medicines m
                    ON m.id = s.user_medicine_id
                LEFT JOIN device_compartments c
                    ON c.device_id = s.device_id
                   AND c.compartment_number =
                       s.compartment_number
                WHERE s.device_id = %s
                  AND s.status = 'active'
                ORDER BY s.reminder_time, s.id
            """, (device_id,))
            rows = cursor.fetchall()

            if len(rows) > MAX_NANO_SCHEDULES:
                conn.rollback()
                return error_response(
                    "TOO_MANY_SCHEDULES",
                    "Nano demo hỗ trợ tối đa 8 lịch.",
                    409
                )

            invalid = [
                int(row["id"]) for row in rows
                if (
                    row["assigned_id"]
                    != row["user_medicine_id"]
                    or not row["slot_enabled"]
                    or not row["medicine_active"]
                    or row["medicine_owner"]
                    != device["owner_user_id"]
                )
            ]

            if invalid:
                conn.rollback()
                return jsonify({
                    "ok": False,
                    "error": "INVALID_ASSIGNMENT",
                    "schedule_ids": invalid,
                    "message": (
                        "Lịch và phân bổ thuốc "
                        "không khớp."
                    )
                }), 409

            result = []
            for row in rows:
                result.append({
                    "id": int(row["id"]),
                    "compartment": int(
                        row["compartment_number"]
                    ),
                    "medicine": {
                        "id": int(
                            row["user_medicine_id"]
                        ),
                        "name": row["medicine_name"],
                        "strength": row["strength"] or "",
                        "dosage_form": (
                            row["dosage_form"] or ""
                        )
                    },
                    "dose": {
                        "quantity": str(
                            row["dose_quantity"]
                        ),
                        "unit": row["dose_unit"]
                    },
                    "time": fmt_time(
                        row["reminder_time"]
                    ),
                    "weekdays_mask": int(
                        row["weekdays_mask"]
                    ),
                    "start_date": (
                        row["start_date"].isoformat()
                    ),
                    "end_date": (
                        row["end_date"].isoformat()
                        if row["end_date"] else None
                    ),
                    "grace_minutes": int(
                        row["grace_minutes"]
                    ),
                    "instructions": (
                        row["instructions"] or ""
                    ),
                    "revision": int(
                        row["revision"] or 0
                    )
                })

            cursor.execute("""
                UPDATE devices
                SET last_seen_at = UTC_TIMESTAMP()
                WHERE id = %s
            """, (device_id,))

            revision = int(
                device["schedule_revision"] or 0
            )

        conn.commit()

        return jsonify({
            "ok": True,
            "revision": revision,
            "timezone": (
                device["timezone"]
                or "Asia/Ho_Chi_Minh"
            ),
            "schedule_count": len(result),
            "schedules": result
        })

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Khong lay duoc lich IoT"
        )
        return error_response(
            "SCHEDULE_ERROR",
            "Không đọc được lịch uống.",
            500
        )
    finally:
        conn.close()

@iot_bp.post("/heartbeat")
def heartbeat():
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return error_response(
            "INVALID_JSON",
            "Cần gửi JSON hợp lệ."
        )

    revision = body.get("installed_revision")

    if (
        type(revision) is not int
        or revision < 0
        or revision > 2147483647
    ):
        return error_response(
            "INVALID_REVISION",
            "Phiên bản lịch không hợp lệ."
        )

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT schedule_revision,
                       operational_status
                FROM devices
                WHERE id = %s
                FOR UPDATE
            """, (g.iot_device["id"],))
            device = cursor.fetchone()

            if (
                not device
                or device["operational_status"] != "active"
            ):
                conn.rollback()
                return error_response(
                    "DEVICE_DISABLED",
                    "Thiết bị đã bị tạm ngừng.",
                    403
                )

            current = int(
                device["schedule_revision"] or 0
            )

            if revision == current:
                cursor.execute("""
                    UPDATE devices
                    SET last_seen_at = UTC_TIMESTAMP(),
                        last_sync_at = UTC_TIMESTAMP()
                    WHERE id = %s
                """, (g.iot_device["id"],))
            else:
                cursor.execute("""
                    UPDATE devices
                    SET last_seen_at = UTC_TIMESTAMP()
                    WHERE id = %s
                """, (g.iot_device["id"],))

        conn.commit()
        return jsonify({
            "ok": True,
            "current_revision": current,
            "installed_revision": revision,
            "update_available": revision != current,
            "synchronized": revision == current
        })

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Loi heartbeat IoT"
        )
        return error_response(
            "HEARTBEAT_ERROR",
            "Không cập nhật được heartbeat.",
            500
        )
    finally:
        conn.close()

@iot_bp.post("/events")
def receive_event():
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return error_response(
            "INVALID_JSON",
            "Dữ liệu sự kiện không hợp lệ."
        )

    uid = str(body.get("event_uid", "")).strip()
    event_type = str(
        body.get("event_type", "")
    ).strip().upper()

    schedule_id = body.get("schedule_id")
    compartment = body.get("compartment")
    event_epoch = body.get("event_epoch")

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{8,96}", uid
    ):
        return error_response(
            "INVALID_UID",
            "Mã sự kiện không hợp lệ."
        )

    if event_type not in EVENT_TYPES:
        return error_response(
            "INVALID_EVENT",
            "Loại sự kiện không hợp lệ."
        )

    if (
        type(schedule_id) is not int
        or schedule_id <= 0
        or type(compartment) is not int
        or compartment not in (1, 2, 3, 4)
    ):
        return error_response(
            "INVALID_DATA",
            "Lịch hoặc ngăn không hợp lệ."
        )

    if event_epoch is not None:
        if (
            type(event_epoch) is not int
            or not 1735689600 <= event_epoch <=
                int(datetime.now(timezone.utc).timestamp()) + 300
        ):
            return error_response(
                "INVALID_TIME",
                "Thời gian sự kiện không hợp lệ."
            )

    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT
                    s.id, s.compartment_number,
                    m.medicine_name
                FROM medicine_schedules s
                JOIN user_medicines m
                    ON m.id = s.user_medicine_id
                WHERE s.id = %s
                  AND s.device_id = %s
                LIMIT 1
            """, (
                schedule_id,
                g.iot_device["id"]
            ))
            schedule = cursor.fetchone()

            if not schedule:
                conn.rollback()
                return error_response(
                    "UNKNOWN_SCHEDULE",
                    "Lịch không thuộc thiết bị này.",
                    409
                )

            expected_slot = int(
                schedule["compartment_number"]
            )

            if (
                event_type == "WRONG"
                and compartment == expected_slot
            ):
                conn.rollback()
                return error_response(
                    "INVALID_COMPARTMENT",
                    "Sự kiện lấy nhầm không khớp.",
                    409
                )

            if (
                event_type != "WRONG"
                and compartment != expected_slot
            ):
                conn.rollback()
                return error_response(
                    "INVALID_COMPARTMENT",
                    "Ngăn không khớp lịch.",
                    409
                )

            observed_at = None
            if event_epoch is not None:
                observed_at = datetime.fromtimestamp(
                    event_epoch, timezone.utc
                ).replace(tzinfo=None)

            cursor.execute("""
                INSERT INTO iot_event_log
                (
                    device_id, event_uid,
                    schedule_id, event_type,
                    compartment_number,
                    medicine_name_snapshot,
                    event_epoch, observed_at,
                    received_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, UTC_TIMESTAMP()
                )
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id)
            """, (
                g.iot_device["id"],
                uid,
                schedule_id,
                event_type,
                compartment,
                schedule["medicine_name"],
                event_epoch,
                observed_at
            ))

            duplicate = cursor.rowcount != 1
            event_id = cursor.lastrowid

            cursor.execute("""
                UPDATE devices
                SET last_seen_at = UTC_TIMESTAMP()
                WHERE id = %s
            """, (g.iot_device["id"],))

        conn.commit()

        return jsonify({
            "ok": True,
            "accepted": True,
            "event_id": event_id,
            "duplicate": duplicate
        })

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Khong luu duoc su kien IoT"
        )
        return error_response(
            "EVENT_ERROR",
            "Không lưu được sự kiện.",
            500
        )
    finally:
        conn.close()
