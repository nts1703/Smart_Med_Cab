
from datetime import timezone
from zoneinfo import ZoneInfo
from flask import (
    Blueprint, render_template, jsonify
)
from flask_login import (
    login_required, current_user
)
from services.database import fetch_all

history_bp = Blueprint("history", __name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

EVENT_LABELS = {
    "REMINDER": "Đã nhắc thuốc",
    "TAKEN": "Đã phát hiện lấy thuốc",
    "WRONG": "Lấy nhầm ngăn",
    "MISSED": "Bỏ lỡ lần nhắc",
    "BUSY": "Tủ đang bận"
}

def load_history():
    rows = fetch_all("""
        SELECT
            e.id,
            e.event_uid,
            e.schedule_id,
            e.event_type,
            e.compartment_number,
            e.medicine_name_snapshot,
            e.observed_at,
            e.received_at,
            d.device_name
        FROM iot_event_log e
        JOIN devices d
          ON d.id = e.device_id
        WHERE d.owner_user_id = %s
        ORDER BY e.id DESC
        LIMIT 100
    """, (current_user.id,))

    items = []

    for row in rows:
        moment = (
            row["observed_at"]
            or row["received_at"]
        )

        # observed_at va received_at duoc
        # luu duoi dang DATETIME UTC.
        local_time = (
            moment.replace(tzinfo=timezone.utc)
            .astimezone(VN_TZ)
        )

        event_type = row["event_type"]

        items.append({
            "id": int(row["id"]),
            "schedule_id": int(
                row["schedule_id"]
            ),
            "event_type": event_type,
            "label": EVENT_LABELS.get(
                event_type, event_type
            ),
            "medicine_name": (
                row["medicine_name_snapshot"]
            ),
            "device_name": row["device_name"],
            "compartment": int(
                row["compartment_number"]
            ),
            "time": local_time.strftime(
                "%d/%m/%Y %H:%M:%S"
            ),
            "time_source": (
                "Cảm biến / Nano"
                if row["observed_at"]
                else "Thời gian server nhận"
            )
        })

    return items

@history_bp.get("/history")
@login_required
def index():
    return render_template(
        "history.html",
        page_title="Lịch sử",
        active_page="history"
    )

@history_bp.get("/history/data")
@login_required
def history_data():
    return jsonify({
        "ok": True,
        "items": load_history()
    })
