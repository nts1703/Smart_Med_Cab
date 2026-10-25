
import os
from flask import Flask, render_template, jsonify
from flask_login import login_required
from flask_wtf.csrf import CSRFProtect
from config import Config
from services.database import (
    test_connection, get_drug_statistics
)
from routes.auth import auth_bp, login_manager
from routes.medicines import medicines_bp
from routes.devices import devices_bp
from routes.compartments import compartments_bp
from routes.schedules import schedules_bp
from routes.iot_api import iot_bp
from routes.history import history_bp

csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    secret = app.config.get("SECRET_KEY")

    if not secret or secret in (
        "replace-with-a-random-secret",
        "local-development-only-change-this-key"
    ):
        raise RuntimeError(
            "Hay cau hinh FLASK_SECRET_KEY trong .env"
        )

    login_manager.init_app(app)
    csrf.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(medicines_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(compartments_bp)
    app.register_blueprint(schedules_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(iot_bp)

    # Thiet bi su dung token API rieng.
    # Khong can CSRF token cua trinh duyet.
    csrf.exempt(iot_bp)

    @app.route("/")
    @login_required
    def dashboard():
        db_connected = False
        statistics = {
            "total_drugs": 0,
            "unverified_drugs": 0,
            "verified_drugs": 0
        }

        try:
            statistics = get_drug_statistics()
            db_connected = True
        except Exception:
            app.logger.exception(
                "Khong doc duoc thong ke MySQL"
            )

        return render_template(
            "dashboard.html",
            page_title="Tổng quan",
            active_page="dashboard",
            db_connected=db_connected,
            statistics=statistics
        )

    @app.route("/api/health")
    def api_health():
        try:
            if test_connection():
                return jsonify({
                    "status": "ok",
                    "database": "connected",
                    "application": "Smart Medicine Cabinet"
                }), 200
        except Exception:
            app.logger.exception(
                "Healthcheck MySQL that bai"
            )

        return jsonify({
            "status": "error",
            "database": "unavailable"
        }), 503

    return app

app = create_app()

# if __name__ == "__main__":
#     port = int(os.getenv(
#         "FLASK_PORT", "5000"
#     ))
#     host = os.getenv(
#         "FLASK_HOST", "127.0.0.1"
#     )

#     app.run(
#         host=host,
#         port=port,
#         debug=app.config["DEBUG"]
#     ) <- code cũ này chỉ hiển thị * Running on http://127.0.0.1:5000
#   code mới bên dưới hiển thị cả: 
# * Running on all addresses (0.0.0.0)
# * Running on http://127.0.0.1:5000
# * Running on http://172.20.10.5:5000

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
