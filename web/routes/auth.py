
import re
import pymysql
from urllib.parse import urlsplit
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, current_app
)
from flask_login import (
    LoginManager, UserMixin, login_user,
    logout_user, login_required, current_user
)
from werkzeug.security import (
    generate_password_hash, check_password_hash
)
from services.database import fetch_one, execute_query

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
login_manager.login_message_category = "warning"

class User(UserMixin):
    def __init__(self, data):
        self.id = int(data["id"])
        self.full_name = data["full_name"]
        self.email = data["email"]
        self.role = data["role"]
        self.account_status = data["account_status"]

    @property
    def is_active(self):
        return self.account_status == "active"

@login_manager.user_loader
def load_user(user_id):
    if not str(user_id).isdigit():
        return None
    data = fetch_one(
        """
        SELECT id, full_name, email, role, account_status
        FROM users
        WHERE id = %s
        LIMIT 1
        """,
        (int(user_id),)
    )
    if not data or data["account_status"] != "active":
        return None
    return User(data)

def safe_next_url(target):
    if not target:
        return None
    if "\\" in target or any(ord(c) < 32 for c in target):
        return None
    parsed = urlsplit(target)
    current = urlsplit(request.host_url)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ("http", "https"):
            return None
        if parsed.netloc != current.netloc:
            return None
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not full_name or len(full_name) > 150:
            flash("Họ tên phải có từ 1 đến 150 ký tự.", "error")
        elif (
            len(email) > 190
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
        ):
            flash("Email không hợp lệ.", "error")
        elif not 8 <= len(password) <= 128:
            flash("Mật khẩu phải có từ 8 đến 128 ký tự.", "error")
        elif password != confirm:
            flash("Mật khẩu xác nhận không khớp.", "error")
        else:
            existing = fetch_one(
                "SELECT id FROM users WHERE email = %s LIMIT 1",
                (email,)
            )
            if existing:
                flash("Email này đã được đăng ký.", "error")
            else:
                hashed = generate_password_hash(
                    password, method="scrypt"
                )
                try:
                    execute_query(
                        """
                        INSERT INTO users
                        (full_name, email, password_hash, role,
                         account_status)
                        VALUES (%s, %s, %s, 'patient', 'active')
                        """,
                        (full_name, email, hashed)
                    )
                except pymysql.err.IntegrityError:
                    flash("Email này đã được đăng ký.", "error")
                else:
                    flash(
                        "Đăng ký thành công! Hãy đăng nhập.",
                        "success"
                    )
                    return redirect(url_for("auth.login"))

    return render_template(
        "register.html",
        page_title="Đăng ký"
    )

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    next_url = request.values.get("next", "")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        row = fetch_one(
            """
            SELECT id, full_name, email, password_hash,
                   role, account_status
            FROM users
            WHERE email = %s
            LIMIT 1
            """,
            (email,)
        )

        if (
            not row
            or row["account_status"] != "active"
            or not check_password_hash(row["password_hash"], password)
        ):
            flash(
                "Email hoặc mật khẩu không chính xác, "
                "hoặc tài khoản đã bị vô hiệu hóa.",
                "error"
            )
        else:
            user = User(row)
            if login_user(user, remember=False):
                try:
                    execute_query(
                        """
                        UPDATE users
                        SET last_login_at = UTC_TIMESTAMP()
                        WHERE id = %s
                        """,
                        (user.id,)
                    )
                except Exception:
                    current_app.logger.exception(
                        "Khong cap nhat duoc last_login_at"
                    )
                flash("Đăng nhập thành công!", "success")
                return redirect(
                    safe_next_url(next_url) or url_for("dashboard")
                )
            flash("Tài khoản hiện không được phép đăng nhập.", "error")

    return render_template(
        "login.html",
        page_title="Đăng nhập",
        next_url=safe_next_url(next_url) or ""
    )

@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Bạn đã đăng xuất.", "success")
    return redirect(url_for("auth.login"))
