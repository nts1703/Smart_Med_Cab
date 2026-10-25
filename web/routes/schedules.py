
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, abort, current_app
)
from flask_login import login_required, current_user
from services.database import (
    get_connection, fetch_one, fetch_all
)

schedules_bp = Blueprint("schedules", __name__)

DAYS = [
    {"bit": 1, "label": "T2"},
    {"bit": 2, "label": "T3"},
    {"bit": 4, "label": "T4"},
    {"bit": 8, "label": "T5"},
    {"bit": 16, "label": "T6"},
    {"bit": 32, "label": "T7"},
    {"bit": 64, "label": "CN"}
]

DOSE_UNITS = [
    "viên", "gói", "ml", "giọt",
    "ống", "muỗng", "lần", "khác"
]

VN_TZ = timezone(timedelta(hours=7))

def today_vn():
    return datetime.now(VN_TZ).date()

def format_time(value):
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return f"{seconds // 3600:02d}:{(seconds // 60) % 60:02d}"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    parts = str(value).split(":")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"

def parse_schedule_form(form):
    raw_quantity = form.get("dose_quantity", "").strip()
    try:
        quantity = Decimal(raw_quantity)
    except (InvalidOperation, ValueError):
        raise ValueError("Liều lượng phải là một số hợp lệ.")

    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity > 9999
        or quantity * 1000 != (quantity * 1000).to_integral_value()
    ):
        raise ValueError(
            "Liều lượng phải lớn hơn 0, tối đa 9999 "
            "và có không quá 3 chữ số thập phân."
        )

    unit = form.get("dose_unit", "").strip()
    if unit not in DOSE_UNITS:
        raise ValueError("Đơn vị liều lượng không hợp lệ.")

    raw_time = form.get("reminder_time", "").strip()
    try:
        reminder_time = datetime.strptime(raw_time, "%H:%M").time()
    except ValueError:
        raise ValueError("Giờ nhắc không hợp lệ.")

    raw_days = form.getlist("days")
    allowed = {str(day["bit"]) for day in DAYS}
    if not raw_days or any(day not in allowed for day in raw_days):
        raise ValueError("Hãy chọn ít nhất một ngày trong tuần.")

    days_mask = sum(int(day) for day in set(raw_days))

    try:
        start_date = date.fromisoformat(
            form.get("start_date", "").strip()
        )
    except ValueError:
        raise ValueError("Ngày bắt đầu không hợp lệ.")

    raw_end = form.get("end_date", "").strip()
    if raw_end:
        try:
            end_date = date.fromisoformat(raw_end)
        except ValueError:
            raise ValueError("Ngày kết thúc không hợp lệ.")
    else:
        end_date = None

    if end_date and end_date < start_date:
        raise ValueError(
            "Ngày kết thúc không được trước ngày bắt đầu."
        )

    if end_date and end_date < today_vn():
        raise ValueError(
            "Ngày kết thúc phải là hôm nay hoặc một ngày tương lai."
        )

    if end_date and not has_occurrence(
        start_date, end_date, days_mask
    ):
        raise ValueError(
            "Khoảng ngày đã chọn không chứa ngày lặp nào."
        )

    try:
        grace = int(form.get("grace_minutes", "15"))
    except ValueError:
        raise ValueError("Thời gian chờ phải là số nguyên.")

    if not 1 <= grace <= 180:
        raise ValueError(
            "Thời gian chờ phải từ 1 đến 180 phút."
        )

    instructions = form.get("instructions", "").strip()
    if len(instructions) > 2000:
        raise ValueError(
            "Ghi chú không được vượt quá 2000 ký tự."
        )

    return {
        "dose_quantity": quantity,
        "dose_unit": unit,
        "reminder_time": reminder_time,
        "weekdays_mask": days_mask,
        "start_date": start_date,
        "end_date": end_date,
        "grace_minutes": grace,
        "instructions": instructions or None
    }

def has_occurrence(start, end, mask):
    for offset in range(7):
        day = start + timedelta(days=offset)
        if end is not None and day > end:
            break
        if mask & (1 << day.weekday()):
            return True
    return False

def find_conflict(
    cursor, device_id, data, exclude_id=None
):
    sql = """
        SELECT id, start_date, end_date, weekdays_mask
        FROM medicine_schedules
        WHERE device_id = %s
          AND status = 'active'
          AND reminder_time = %s
          AND (weekdays_mask & %s) <> 0
          AND start_date <= %s
          AND (
              end_date IS NULL
              OR end_date >= %s
          )
    """
    new_end = data["end_date"] or date(9999, 12, 31)
    params = [
        device_id,
        data["reminder_time"],
        data["weekdays_mask"],
        new_end,
        data["start_date"]
    ]

    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)

    cursor.execute(sql, tuple(params))
    candidates = cursor.fetchall()

    for old in candidates:
        overlap_start = max(
            old["start_date"],
            data["start_date"]
        )
        ends = [
            value for value in
            (old["end_date"], data["end_date"])
            if value is not None
        ]
        overlap_end = min(ends) if ends else None
        common_days = (
            int(old["weekdays_mask"])
            & data["weekdays_mask"]
        )

        if (
            overlap_end is None
            or overlap_end >= overlap_start
        ) and has_occurrence(
            overlap_start, overlap_end, common_days
        ):
            return old["id"]

    return None

def increment_device_revision(cursor, device_id):
    cursor.execute("""
        UPDATE devices
        SET schedule_revision =
            COALESCE(schedule_revision, 0) + 1
        WHERE id = %s
    """, (device_id,))

def get_owned_schedule(schedule_id):
    row = fetch_one("""
        SELECT s.id, s.device_id
        FROM medicine_schedules s
        JOIN devices d ON d.id = s.device_id
        WHERE s.id = %s
          AND d.owner_user_id = %s
        LIMIT 1
    """, (schedule_id, current_user.id))
    if not row:
        abort(404)
    return row

def lock_device(cursor, device_id):
    cursor.execute("""
        SELECT id, operational_status
        FROM devices
        WHERE id = %s AND owner_user_id = %s
        FOR UPDATE
    """, (device_id, current_user.id))
    device = cursor.fetchone()
    if not device:
        raise ValueError("Bạn không có quyền quản lý tủ này.")
    return device

def lock_owned_schedule(cursor, schedule_id, device_id):
    cursor.execute("""
        SELECT s.*
        FROM medicine_schedules s
        JOIN devices d ON d.id = s.device_id
        WHERE s.id = %s
          AND s.device_id = %s
          AND d.owner_user_id = %s
        FOR UPDATE
    """, (schedule_id, device_id, current_user.id))
    schedule = cursor.fetchone()
    if not schedule:
        raise ValueError("Không tìm thấy lịch uống thuốc.")
    return schedule

def check_assignment(cursor, schedule):
    cursor.execute("""
        SELECT c.user_medicine_id, c.is_enabled,
               m.is_active
        FROM device_compartments c
        LEFT JOIN user_medicines m
            ON m.id = c.user_medicine_id
        WHERE c.device_id = %s
          AND c.compartment_number = %s
        LIMIT 1
    """, (
        schedule["device_id"],
        schedule["compartment_number"]
    ))
    slot = cursor.fetchone()

    if (
        not slot
        or not slot["is_enabled"]
        or slot["user_medicine_id"] != schedule["user_medicine_id"]
        or not slot["is_active"]
    ):
        raise ValueError(
            "Thuốc trong ngăn đã thay đổi, bị lưu trữ "
            "hoặc ngăn đã vô hiệu hóa. "
            "Hãy kiểm tra lại phân bổ thuốc."
        )

@schedules_bp.get("/schedules")
@login_required
def index():
    slots = fetch_all("""
        SELECT
            c.id AS slot_id,
            c.device_id,
            c.compartment_number,
            d.device_name,
            m.medicine_name,
            m.strength
        FROM device_compartments c
        JOIN devices d ON d.id = c.device_id
        JOIN user_medicines m ON m.id = c.user_medicine_id
        WHERE d.owner_user_id = %s
          AND d.operational_status = 'active'
          AND c.is_enabled = 1
          AND m.user_id = %s
          AND m.is_active = 1
        ORDER BY d.device_name, c.compartment_number
    """, (current_user.id, current_user.id))

    schedules = fetch_all("""
        SELECT
            s.*, d.device_name,
            d.operational_status,
            m.medicine_name, m.strength,
            c.user_medicine_id AS assigned_medicine_id
        FROM medicine_schedules s
        JOIN devices d ON d.id = s.device_id
        JOIN user_medicines m ON m.id = s.user_medicine_id
        LEFT JOIN device_compartments c
          ON c.device_id = s.device_id
         AND c.compartment_number = s.compartment_number
        WHERE d.owner_user_id = %s
        ORDER BY
            CASE s.status
                WHEN 'active' THEN 0
                WHEN 'paused' THEN 1
                ELSE 2
            END,
            s.reminder_time ASC,
            s.id DESC
    """, (current_user.id,))

    for schedule in schedules:
        schedule["time_hhmm"] = format_time(
            schedule["reminder_time"]
        )
        mask = int(schedule["weekdays_mask"])
        schedule["selected_days"] = {
            day["bit"] for day in DAYS
            if mask & day["bit"]
        }
        schedule["days_label"] = (
            "Hằng ngày" if mask == 127 else ", ".join(
                day["label"] for day in DAYS
                if mask & day["bit"]
            )
        )
        schedule["assignment_matches"] = (
            schedule["assigned_medicine_id"]
            == schedule["user_medicine_id"]
        )

    return render_template(
        "schedules.html",
        page_title="Lịch uống thuốc",
        active_page="schedules",
        slots=slots,
        schedules=schedules,
        days=DAYS,
        dose_units=DOSE_UNITS,
        today_iso=today_vn().isoformat(),
        total_active=sum(
            s["status"] == "active"
            for s in schedules
        ),
        total_paused=sum(
            s["status"] == "paused"
            for s in schedules
        )
    )

@schedules_bp.post("/schedules/create")
@login_required
def create():
    try:
        data = parse_schedule_form(request.form)
        raw_slot = request.form.get("slot_id", "").strip()
        if not raw_slot.isdigit() or int(raw_slot) <= 0:
            raise ValueError(
                "Hãy chọn ngăn đã được phân bổ thuốc."
            )
        slot_id = int(raw_slot)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("schedules.index"))

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT c.device_id
                FROM device_compartments c
                JOIN devices d ON d.id = c.device_id
                WHERE c.id = %s
                  AND d.owner_user_id = %s
                LIMIT 1
            """, (slot_id, current_user.id))
            candidate = cursor.fetchone()
            if not candidate:
                raise ValueError("Ngăn thuốc không hợp lệ.")

            device_id = candidate["device_id"]
            device = lock_device(cursor, device_id)
            if device["operational_status"] != "active":
                raise ValueError(
                    "Thiết bị đã tạm ngừng hoạt động."
                )

            cursor.execute("""
                SELECT
                    c.device_id,
                    c.compartment_number,
                    c.user_medicine_id,
                    c.is_enabled,
                    m.is_active
                FROM device_compartments c
                JOIN user_medicines m
                    ON m.id = c.user_medicine_id
                WHERE c.id = %s
                  AND c.device_id = %s
                  AND m.user_id = %s
                FOR UPDATE
            """, (
                slot_id, device_id, current_user.id
            ))
            slot = cursor.fetchone()

            if (
                not slot
                or not slot["is_enabled"]
                or not slot["is_active"]
            ):
                raise ValueError(
                    "Ngăn chưa được gán thuốc đang sử dụng."
                )

            if find_conflict(cursor, device_id, data):
                raise ValueError(
                    "Đã có lịch nhắc khác trên cùng tủ "
                    "vào thời điểm và ngày này. "
                    "Hãy chọn giờ khác."
                )

            cursor.execute("""
                INSERT INTO medicine_schedules
                (
                    user_medicine_id,
                    device_id,
                    compartment_number,
                    dose_quantity,
                    dose_unit,
                    instructions,
                    reminder_time,
                    weekdays_mask,
                    start_date,
                    end_date,
                    grace_minutes,
                    status,
                    revision
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'active', 1
                )
            """, (
                slot["user_medicine_id"],
                device_id,
                slot["compartment_number"],
                data["dose_quantity"],
                data["dose_unit"],
                data["instructions"],
                data["reminder_time"],
                data["weekdays_mask"],
                data["start_date"],
                data["end_date"],
                data["grace_minutes"]
            ))

            increment_device_revision(cursor, device_id)

        connection.commit()
        flash("Đã tạo lịch uống thuốc thành công.", "success")

    except ValueError as exc:
        connection.rollback()
        flash(str(exc), "warning")
    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Khong tao duoc lich uong thuoc"
        )
        flash(
            "Có lỗi khi tạo lịch. Hãy kiểm tra MySQL "
            "và xem thông báo trong Terminal.",
            "error"
        )
    finally:
        connection.close()

    return redirect(url_for("schedules.index"))

@schedules_bp.post("/schedules/<int:schedule_id>/edit")
@login_required
def edit(schedule_id):
    owned = get_owned_schedule(schedule_id)

    try:
        data = parse_schedule_form(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("schedules.index"))

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            device = lock_device(
                cursor, owned["device_id"]
            )
            schedule = lock_owned_schedule(
                cursor, schedule_id, owned["device_id"]
            )

            if schedule["status"] == "completed":
                raise ValueError(
                    "Lịch đã kết thúc. Hãy tạo lịch mới."
                )

            check_assignment(cursor, schedule)

            if schedule["status"] == "active":
                if device["operational_status"] != "active":
                    raise ValueError(
                        "Thiết bị đang tạm ngừng sử dụng."
                    )
                if find_conflict(
                    cursor,
                    schedule["device_id"],
                    data,
                    exclude_id=schedule_id
                ):
                    raise ValueError(
                        "Giờ nhắc mới trùng với lịch khác "
                        "trên cùng tủ."
                    )

            cursor.execute("""
                UPDATE medicine_schedules
                SET dose_quantity = %s,
                    dose_unit = %s,
                    instructions = %s,
                    reminder_time = %s,
                    weekdays_mask = %s,
                    start_date = %s,
                    end_date = %s,
                    grace_minutes = %s,
                    revision = revision + 1
                WHERE id = %s
            """, (
                data["dose_quantity"],
                data["dose_unit"],
                data["instructions"],
                data["reminder_time"],
                data["weekdays_mask"],
                data["start_date"],
                data["end_date"],
                data["grace_minutes"],
                schedule_id
            ))

            increment_device_revision(
                cursor, schedule["device_id"]
            )

        connection.commit()
        flash("Đã cập nhật lịch uống thuốc.", "success")

    except ValueError as exc:
        connection.rollback()
        flash(str(exc), "warning")
    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Khong sua duoc lich uong thuoc"
        )
        flash("Có lỗi khi cập nhật lịch uống.", "error")
    finally:
        connection.close()

    return redirect(url_for("schedules.index"))

def change_status(schedule_id, action):
    owned = get_owned_schedule(schedule_id)
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            device = lock_device(
                cursor, owned["device_id"]
            )
            schedule = lock_owned_schedule(
                cursor, schedule_id, owned["device_id"]
            )
            old_status = schedule["status"]

            if action == "pause":
                if old_status != "active":
                    raise ValueError(
                        "Chỉ lịch đang hoạt động mới có thể tạm dừng."
                    )
                new_status = "paused"
                message = "Đã tạm dừng lịch uống thuốc."

            elif action == "resume":
                if old_status != "paused":
                    raise ValueError(
                        "Chỉ lịch tạm dừng mới có thể kích hoạt lại."
                    )
                if device["operational_status"] != "active":
                    raise ValueError(
                        "Hãy kích hoạt tủ thuốc trước."
                    )
                if (
                    schedule["end_date"] is not None
                    and schedule["end_date"] < today_vn()
                ):
                    raise ValueError(
                        "Lịch đã quá ngày kết thúc. "
                        "Hãy sửa thời hạn trước khi kích hoạt."
                    )

                check_assignment(cursor, schedule)

                data = {
                    "reminder_time": schedule["reminder_time"],
                    "weekdays_mask": int(
                        schedule["weekdays_mask"]
                    ),
                    "start_date": schedule["start_date"],
                    "end_date": schedule["end_date"]
                }
                if find_conflict(
                    cursor,
                    schedule["device_id"],
                    data,
                    exclude_id=schedule_id
                ):
                    raise ValueError(
                        "Lịch đang trùng giờ với lịch khác. "
                        "Hãy sửa thời gian trước khi kích hoạt."
                    )

                new_status = "active"
                message = "Đã kích hoạt lại lịch uống thuốc."

            elif action == "complete":
                if old_status == "completed":
                    raise ValueError(
                        "Lịch này đã kết thúc."
                    )
                new_status = "completed"
                message = "Đã kết thúc lịch uống thuốc."

            else:
                raise ValueError("Thao tác không hợp lệ.")

            cursor.execute("""
                UPDATE medicine_schedules
                SET status = %s,
                    revision = revision + 1
                WHERE id = %s
            """, (new_status, schedule_id))

            increment_device_revision(
                cursor, schedule["device_id"]
            )

        connection.commit()
        flash(message, "success")

    except ValueError as exc:
        connection.rollback()
        flash(str(exc), "warning")
    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Khong thay doi duoc trang thai lich"
        )
        flash("Có lỗi khi thay đổi trạng thái lịch.", "error")
    finally:
        connection.close()

    return redirect(url_for("schedules.index"))

@schedules_bp.post("/schedules/<int:schedule_id>/pause")
@login_required
def pause(schedule_id):
    return change_status(schedule_id, "pause")

@schedules_bp.post("/schedules/<int:schedule_id>/resume")
@login_required
def resume(schedule_id):
    return change_status(schedule_id, "resume")

@schedules_bp.post("/schedules/<int:schedule_id>/complete")
@login_required
def complete(schedule_id):
    return change_status(schedule_id, "complete")
