
import pymysql
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, abort, current_app
)
from flask_login import login_required, current_user
from services.database import get_connection, fetch_one, fetch_all

compartments_bp = Blueprint("compartments", __name__)

@compartments_bp.get("/compartments")
@login_required
def index():
    devices = fetch_all("""
        SELECT id, device_name, device_code,
               operational_status, schedule_revision
        FROM devices
        WHERE owner_user_id = %s
        ORDER BY id DESC
    """, (current_user.id,))

    medicines = fetch_all("""
        SELECT id, medicine_name, strength, dosage_form
        FROM user_medicines
        WHERE user_id = %s AND is_active = 1
        ORDER BY medicine_name ASC, id ASC
    """, (current_user.id,))

    rows = fetch_all("""
        SELECT
            c.id, c.device_id, c.compartment_number,
            c.compartment_name, c.is_enabled,
            c.user_medicine_id,
            m.medicine_name AS assigned_name,
            m.strength AS assigned_strength,
            m.is_active AS medicine_active,
            (
                SELECT COUNT(*)
                FROM medicine_schedules s
                WHERE s.device_id = c.device_id
                  AND s.compartment_number = c.compartment_number
                  AND s.status = 'active'
            ) AS active_schedule_count
        FROM device_compartments c
        JOIN devices d ON d.id = c.device_id
        LEFT JOIN user_medicines m
            ON m.id = c.user_medicine_id
        WHERE d.owner_user_id = %s
        ORDER BY c.device_id DESC, c.compartment_number ASC
    """, (current_user.id,))

    assignments = {}
    for row in rows:
        assignments.setdefault(row["device_id"], []).append(row)

    history = fetch_all("""
        SELECT
            h.compartment_number,
            h.previous_medicine_name,
            h.new_medicine_name,
            h.changed_at,
            d.device_name
        FROM compartment_assignment_history h
        JOIN devices d ON d.id = h.device_id
        WHERE d.owner_user_id = %s
        ORDER BY h.id DESC
        LIMIT 20
    """, (current_user.id,))

    return render_template(
        "compartments.html",
        page_title="Phân bổ thuốc",
        active_page="compartments",
        devices=devices,
        medicines=medicines,
        active_medicine_ids={m["id"] for m in medicines},
        assignments=assignments,
        history=history
    )

@compartments_bp.post(
    "/devices/<int:device_id>/compartments/<int:number>/assign"
)
@login_required
def assign(device_id, number):
    if number not in (1, 2, 3, 4):
        abort(404)

    owner = fetch_one("""
        SELECT id FROM devices
        WHERE id = %s AND owner_user_id = %s
        LIMIT 1
    """, (device_id, current_user.id))
    if not owner:
        abort(404)

    raw_id = request.form.get("user_medicine_id", "").strip()
    if raw_id:
        if not raw_id.isdigit() or int(raw_id) <= 0:
            flash("Thuốc được chọn không hợp lệ.", "error")
            return redirect(url_for("compartments.index"))
        new_id = int(raw_id)
    else:
        new_id = None

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            # Khoa thiet bi de tranh 2 yeu cau gan cung luc.
            cursor.execute("""
                SELECT id FROM devices
                WHERE id = %s AND owner_user_id = %s
                FOR UPDATE
            """, (device_id, current_user.id))
            if not cursor.fetchone():
                raise ValueError("Thiết bị không còn tồn tại.")

            cursor.execute("""
                SELECT c.user_medicine_id, c.is_enabled,
                       m.medicine_name AS old_name
                FROM device_compartments c
                LEFT JOIN user_medicines m
                    ON m.id = c.user_medicine_id
                WHERE c.device_id = %s
                  AND c.compartment_number = %s
                FOR UPDATE
            """, (device_id, number))
            slot = cursor.fetchone()
            if not slot:
                raise ValueError("Không tìm thấy ngăn thuốc.")

            if not slot["is_enabled"]:
                raise ValueError(
                    "Ngăn này đang bị vô hiệu hóa, không thể gán thuốc."
                )

            old_id = slot["user_medicine_id"]
            old_name = slot["old_name"]

            if old_id == new_id:
                connection.rollback()
                flash("Ngăn thuốc không có thay đổi.", "warning")
                return redirect(url_for("compartments.index"))

            new_name = None
            if new_id is not None:
                cursor.execute("""
                    SELECT id, medicine_name
                    FROM user_medicines
                    WHERE id = %s
                      AND user_id = %s
                      AND is_active = 1
                    LIMIT 1
                """, (new_id, current_user.id))
                medicine = cursor.fetchone()
                if not medicine:
                    raise ValueError(
                        "Thuốc không tồn tại hoặc đã được lưu trữ."
                    )
                new_name = medicine["medicine_name"]

                # Mot thuoc chi gan mot ngan tren cung mot tu.
                cursor.execute("""
                    SELECT compartment_number
                    FROM device_compartments
                    WHERE device_id = %s
                      AND user_medicine_id = %s
                      AND compartment_number <> %s
                    LIMIT 1
                """, (device_id, new_id, number))
                other = cursor.fetchone()
                if other:
                    raise ValueError(
                        f"Thuốc này đã được gán vào ngăn "
                        f"{other['compartment_number']} của tủ."
                    )

            # Khong thay doi thuoc trong ngan dang co lich chay.
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM medicine_schedules
                WHERE device_id = %s
                  AND compartment_number = %s
                  AND status = 'active'
            """, (device_id, number))
            schedule = cursor.fetchone()
            if int(schedule["total"] or 0) > 0:
                raise ValueError(
                    "Ngăn này đang có lịch nhắc hoạt động. "
                    "Hãy tạm dừng các lịch trước khi đổi hoặc bỏ thuốc."
                )

            cursor.execute("""
                UPDATE device_compartments
                SET user_medicine_id = %s
                WHERE device_id = %s
                  AND compartment_number = %s
            """, (new_id, device_id, number))

            cursor.execute("""
                INSERT INTO compartment_assignment_history
                (
                    device_id,
                    compartment_number,
                    previous_user_medicine_id,
                    new_user_medicine_id,
                    previous_medicine_name,
                    new_medicine_name,
                    changed_by_user_id,
                    changed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        UTC_TIMESTAMP())
            """, (
                device_id, number, old_id, new_id,
                old_name, new_name, current_user.id
            ))

            # Danh dau du lieu phan bo da thay doi.
            cursor.execute("""
                UPDATE devices
                SET schedule_revision = COALESCE(schedule_revision, 0) + 1
                WHERE id = %s
            """, (device_id,))

        connection.commit()
        if new_id is None:
            flash(
                f"Đã bỏ gán thuốc khỏi ngăn {number}. "
                "Hãy kiểm tra ngăn thuốc vật lý.",
                "success"
            )
        else:
            flash(
                f"Đã gán '{new_name}' vào ngăn {number}. "
                "Hãy đặt đúng thuốc vào ngăn thực tế.",
                "success"
            )

    except ValueError as exc:
        connection.rollback()
        flash(str(exc), "warning")
    except pymysql.err.IntegrityError:
        connection.rollback()
        current_app.logger.exception("Loi rang buoc gan thuoc")
        flash(
            "Không thể lưu phân bổ vì dữ liệu có xung đột.",
            "error"
        )
    except Exception:
        connection.rollback()
        current_app.logger.exception("Loi phan bo thuoc")
        flash(
            "Có lỗi khi lưu phân bổ thuốc. "
            "Không có thay đổi nào được lưu.",
            "error"
        )
    finally:
        connection.close()

    return redirect(url_for("compartments.index"))
