
import secrets
import pymysql
from collections import defaultdict
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, abort, current_app
)
from flask_login import login_required, current_user
from services.database import (
    get_connection, fetch_one, fetch_all, execute_query
)

devices_bp = Blueprint("devices", __name__)

def get_owned_device(device_id):
    device = fetch_one("""
        SELECT id, owner_user_id, device_code, device_name,
               operational_status, notes
        FROM devices
        WHERE id = %s AND owner_user_id = %s
        LIMIT 1
    """, (device_id, current_user.id))
    if not device:
        abort(404)
    return device

@devices_bp.get("/devices")
@login_required
def index():
    devices = fetch_all("""
        SELECT
            d.id,
            d.device_code,
            d.device_name,
            d.hardware_model,
            d.esp_firmware_version,
            d.nano_firmware_version,
            d.operational_status,
            d.last_seen_at,
            d.last_sync_at,
            d.schedule_revision,
            d.notes,
            d.created_at,
            CASE
                WHEN d.operational_status = 'active'
                 AND d.last_seen_at IS NOT NULL
                 AND d.last_seen_at >= DATE_SUB(
                     UTC_TIMESTAMP(), INTERVAL 3 MINUTE
                 )
                THEN 1 ELSE 0
            END AS is_online,
            (
                SELECT COUNT(*)
                FROM medicine_schedules s
                WHERE s.device_id = d.id
                  AND s.status = 'active'
            ) AS active_schedules
        FROM devices d
        WHERE d.owner_user_id = %s
        ORDER BY d.created_at DESC, d.id DESC
    """, (current_user.id,))

    compartments = fetch_all("""
        SELECT
            c.id,
            c.device_id,
            c.compartment_number,
            c.compartment_name,
            c.is_enabled
        FROM device_compartments c
        JOIN devices d ON d.id = c.device_id
        WHERE d.owner_user_id = %s
        ORDER BY c.device_id, c.compartment_number
    """, (current_user.id,))

    compartments_by_device = defaultdict(list)
    for compartment in compartments:
        compartments_by_device[
            compartment["device_id"]
        ].append(compartment)

    return render_template(
        "devices.html",
        page_title="Thiết bị IoT",
        active_page="devices",
        devices=devices,
        compartments_by_device=compartments_by_device
    )

@devices_bp.post("/devices/create")
@login_required
def create_device():
    name = request.form.get(
        "device_name", ""
    ).strip()
    notes = request.form.get(
        "notes", ""
    ).strip()

    if not 2 <= len(name) <= 150:
        flash(
            "Tên thiết bị phải có từ 2 đến 150 ký tự.",
            "error"
        )
        return redirect(url_for("devices.index"))

    if len(notes) > 1000:
        flash(
            "Ghi chú không được vượt quá 1000 ký tự.",
            "error"
        )
        return redirect(url_for("devices.index"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            # Ma tu duoc tao ngau nhien.
            # Khong phai token xac thuc ESP32.
            device_code = (
                "SMC-" + secrets.token_hex(8).upper()
            )

            cursor.execute("""
                INSERT INTO devices
                (
                    owner_user_id,
                    device_code,
                    device_name,
                    hardware_model,
                    operational_status,
                    notes
                )
                VALUES
                (
                    %s, %s, %s,
                    'ESP32-CAM + Arduino Nano',
                    'active', %s
                )
            """, (
                current_user.id,
                device_code,
                name,
                notes or None
            ))

            device_id = cursor.lastrowid

            # Tu dong tao 4 ngan thuoc.
            compartment_data = [
                (
                    device_id,
                    number,
                    f"Ngăn {number}",
                    1
                )
                for number in range(1, 5)
            ]

            cursor.executemany("""
                INSERT INTO device_compartments
                (
                    device_id,
                    compartment_number,
                    compartment_name,
                    is_enabled
                )
                VALUES (%s, %s, %s, %s)
            """, compartment_data)

            # Gan quyen owner cho tai khoan tao tu.
            cursor.execute("""
                INSERT INTO device_access
                (
                    device_id,
                    user_id,
                    access_role,
                    is_active
                )
                VALUES (%s, %s, 'owner', 1)
            """, (
                device_id,
                current_user.id
            ))

        connection.commit()

        flash(
            "Đã tạo tủ thuốc và 4 ngăn thành công. "
            "Thiết bị chưa được ghép nối với ESP32-CAM.",
            "success"
        )

    except pymysql.err.IntegrityError:
        connection.rollback()
        current_app.logger.exception(
            "Loi rang buoc khi tao thiet bi"
        )
        flash(
            "Không thể tạo thiết bị do xung đột dữ liệu. "
            "Vui lòng thử lại.",
            "error"
        )

    except Exception:
        connection.rollback()
        current_app.logger.exception(
            "Khong the tao thiet bi"
        )
        flash(
            "Có lỗi khi tạo thiết bị. "
            "Không có dữ liệu tạo dở được lưu.",
            "error"
        )

    finally:
        connection.close()

    return redirect(url_for("devices.index"))

@devices_bp.post("/devices/<int:device_id>/edit")
@login_required
def edit_device(device_id):
    get_owned_device(device_id)

    name = request.form.get(
        "device_name", ""
    ).strip()
    notes = request.form.get(
        "notes", ""
    ).strip()

    if not 2 <= len(name) <= 150:
        flash(
            "Tên thiết bị phải có từ 2 đến 150 ký tự.",
            "error"
        )
        return redirect(url_for("devices.index"))

    if len(notes) > 1000:
        flash(
            "Ghi chú không được vượt quá 1000 ký tự.",
            "error"
        )
        return redirect(url_for("devices.index"))

    execute_query("""
        UPDATE devices
        SET
            device_name = %s,
            notes = %s
        WHERE id = %s
          AND owner_user_id = %s
    """, (
        name,
        notes or None,
        device_id,
        current_user.id
    ))

    flash(
        "Đã cập nhật thông tin thiết bị.",
        "success"
    )

    return redirect(url_for("devices.index"))

@devices_bp.post("/devices/<int:device_id>/toggle")
@login_required
def toggle_device(device_id):
    device = get_owned_device(device_id)

    if device["operational_status"] == "active":
        schedules = fetch_one("""
            SELECT COUNT(*) AS total
            FROM medicine_schedules
            WHERE device_id = %s
              AND status = 'active'
        """, (device_id,))

        if int(schedules["total"] or 0) > 0:
            flash(
                "Tủ vẫn có lịch nhắc đang hoạt động. "
                "Hãy tạm dừng các lịch trước khi "
                "ngừng sử dụng thiết bị.",
                "warning"
            )
            return redirect(url_for("devices.index"))

        new_status = "disabled"
        message = "Đã tạm ngừng sử dụng thiết bị."

    else:
        new_status = "active"
        message = "Đã kích hoạt lại thiết bị."

    execute_query("""
        UPDATE devices
        SET operational_status = %s
        WHERE id = %s
          AND owner_user_id = %s
    """, (
        new_status,
        device_id,
        current_user.id
    ))

    flash(message, "success")
    return redirect(url_for("devices.index"))
