
import pymysql
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, jsonify, abort
)
from flask_login import login_required, current_user
from services.database import fetch_one, fetch_all, execute_query
from services.drug_search import search_catalog

medicines_bp = Blueprint("medicines", __name__)

def get_owned_medicine(medicine_id):
    row = fetch_one("""
        SELECT id, user_id, drug_catalog_id, medicine_name,
               strength, dosage_form, entry_source, notes, is_active
        FROM user_medicines
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (medicine_id, current_user.id))
    if not row:
        abort(404)
    return row

def validate_text(value, limit):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValueError(f"Nội dung vượt quá {limit} ký tự.")
    return value

def return_to_list():
    if request.form.get("return_view") == "archived":
        return redirect(url_for("medicines.index", view="archived"))
    return redirect(url_for("medicines.index"))

@medicines_bp.get("/medicines")
@login_required
def index():
    show_archived = request.args.get("view") == "archived"
    rows = fetch_all("""
        SELECT
            m.id, m.drug_catalog_id, m.medicine_name,
            m.strength, m.dosage_form, m.entry_source,
            m.notes, m.is_active, m.created_at,
            c.registration_number,
            CASE
                WHEN c.registration_status = 'valid'
                 AND c.review_status = 'reviewed'
                 AND c.is_searchable = 1
                THEN 1 ELSE 0
            END AS catalog_verified
        FROM user_medicines m
        LEFT JOIN drug_catalog c
            ON m.drug_catalog_id = c.id
        WHERE m.user_id = %s AND m.is_active = %s
        ORDER BY m.created_at DESC, m.id DESC
    """, (current_user.id, 0 if show_archived else 1))
    counts = fetch_one("""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(
                CASE WHEN is_active = 1 THEN 1 ELSE 0 END
            ), 0) AS active_total,
            COALESCE(SUM(
                CASE WHEN is_active = 0 THEN 1 ELSE 0 END
            ), 0) AS archived_total
        FROM user_medicines
        WHERE user_id = %s
    """, (current_user.id,))
    return render_template(
        "medicines.html",
        page_title="Quản lý thuốc",
        active_page="medicines",
        medicines=rows,
        counts=counts,
        show_archived=show_archived
    )

@medicines_bp.get("/api/drugs/search")
@login_required
def drug_search_api():
    keyword = request.args.get("q", "")
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    result = search_catalog(
        keyword=keyword,
        page=page,
        page_size=15
    )
    return jsonify(result)

@medicines_bp.post("/medicines/add")
@login_required
def add_medicine():
    mode = request.form.get("mode", "catalog")
    try:
        notes = validate_text(request.form.get("notes", ""), 2000)
        if mode == "catalog":
            raw_id = request.form.get("drug_catalog_id", "")
            if not raw_id.isdigit() or int(raw_id) <= 0:
                raise ValueError("Bạn chưa chọn thuốc từ danh mục.")
            drug = fetch_one("""
                SELECT id, drug_name, strength, dosage_form,
                       registration_status, review_status, is_searchable
                FROM drug_catalog
                WHERE id = %s
                LIMIT 1
            """, (int(raw_id),))
            if not drug:
                raise ValueError("Thuốc không tồn tại trong danh mục.")
            catalog_id = drug["id"]
            medicine_name = drug["drug_name"]
            strength = drug["strength"] or ""
            dosage_form = drug["dosage_form"] or ""
            entry_source = "catalog"
        elif mode == "manual":
            medicine_name = validate_text(
                request.form.get("medicine_name", ""), 255
            )
            strength = validate_text(
                request.form.get("strength", ""), 255
            )
            dosage_form = validate_text(
                request.form.get("dosage_form", ""), 255
            )
            if not medicine_name:
                raise ValueError("Tên thuốc không được để trống.")
            catalog_id = None
            entry_source = "manual"
        else:
            raise ValueError("Cách nhập thuốc không hợp lệ.")
        execute_query("""
            INSERT INTO user_medicines
            (user_id, drug_catalog_id, medicine_name,
             strength, dosage_form, entry_source, notes, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
        """, (
            current_user.id, catalog_id, medicine_name,
            strength or None, dosage_form or None,
            entry_source, notes or None
        ))
        if entry_source == "catalog":
            verified = (
                drug["registration_status"] == "valid"
                and drug["review_status"] == "reviewed"
                and int(drug["is_searchable"] or 0) == 1
            )
            if verified:
                flash("Đã thêm thuốc thành công.", "success")
            else:
                flash(
                    "Đã thêm thuốc. Thông tin đăng ký "
                    "của sản phẩm này chưa được xác minh.",
                    "warning"
                )
        else:
            flash(
                "Đã thêm thuốc nhập thủ công. Hãy đối chiếu "
                "thông tin với bao bì hoặc đơn thuốc.",
                "success"
            )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("medicines.index"))

@medicines_bp.post("/medicines/<int:medicine_id>/edit")
@login_required
def edit_medicine(medicine_id):
    medicine = get_owned_medicine(medicine_id)
    try:
        notes = validate_text(request.form.get("notes", ""), 2000)
        if medicine["entry_source"] == "manual":
            name = validate_text(
                request.form.get("medicine_name", ""), 255
            )
            strength = validate_text(
                request.form.get("strength", ""), 255
            )
            dosage_form = validate_text(
                request.form.get("dosage_form", ""), 255
            )
            if not name:
                raise ValueError("Tên thuốc không được để trống.")
            execute_query("""
                UPDATE user_medicines
                SET medicine_name = %s,
                    strength = %s,
                    dosage_form = %s,
                    notes = %s
                WHERE id = %s AND user_id = %s
            """, (
                name, strength or None, dosage_form or None,
                notes or None, medicine_id, current_user.id
            ))
        else:
            execute_query("""
                UPDATE user_medicines
                SET notes = %s
                WHERE id = %s AND user_id = %s
            """, (notes or None, medicine_id, current_user.id))
        flash("Đã cập nhật thông tin thuốc.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("medicines.index"))

@medicines_bp.post("/medicines/<int:medicine_id>/archive")
@login_required
def archive_medicine(medicine_id):
    medicine = get_owned_medicine(medicine_id)
    if not medicine["is_active"]:
        return redirect(url_for("medicines.index"))
    active_schedules = fetch_one("""
        SELECT COUNT(*) AS total
        FROM medicine_schedules
        WHERE user_medicine_id = %s
          AND status = 'active'
    """, (medicine_id,))
    if active_schedules["total"] > 0:
        flash(
            "Thuốc còn lịch nhắc đang hoạt động. "
            "Hãy tạm dừng lịch trước khi lưu trữ.",
            "warning"
        )
    else:
        execute_query("""
            UPDATE user_medicines
            SET is_active = 0
            WHERE id = %s AND user_id = %s
        """, (medicine_id, current_user.id))
        flash("Đã chuyển thuốc vào mục lưu trữ.", "success")
    return redirect(url_for("medicines.index"))

@medicines_bp.post("/medicines/<int:medicine_id>/restore")
@login_required
def restore_medicine(medicine_id):
    get_owned_medicine(medicine_id)
    execute_query("""
        UPDATE user_medicines
        SET is_active = 1
        WHERE id = %s AND user_id = %s
    """, (medicine_id, current_user.id))
    flash("Đã khôi phục thuốc.", "success")
    return redirect(url_for("medicines.index", view="archived"))

@medicines_bp.post("/medicines/<int:medicine_id>/delete")
@login_required
def delete_medicine(medicine_id):
    medicine = get_owned_medicine(medicine_id)
    schedule_count = fetch_one("""
        SELECT COUNT(*) AS total
        FROM medicine_schedules
        WHERE user_medicine_id = %s
    """, (medicine_id,))
    if schedule_count["total"] > 0:
        flash(
            "Không thể xóa vĩnh viễn thuốc này vì đã có lịch "
            "uống liên quan. Hãy tạm dừng lịch đang hoạt động "
            "và dùng chức năng Lưu trữ để giữ lịch sử.",
            "warning"
        )
        return return_to_list()
    try:
        result = execute_query("""
            DELETE FROM user_medicines
            WHERE id = %s AND user_id = %s
        """, (medicine_id, current_user.id))
    except pymysql.err.IntegrityError:
        flash(
            "Không thể xóa vì thuốc đang có dữ liệu liên quan. "
            "Hãy sử dụng chức năng Lưu trữ.",
            "warning"
        )
        return return_to_list()
    if result["affected_rows"] == 1:
        flash(
            f"Đã xóa vĩnh viễn thuốc '{medicine['medicine_name']}' "
            "khỏi tài khoản của bạn.",
            "success"
        )
    else:
        flash("Không tìm thấy thuốc cần xóa.", "error")
    return return_to_list()
