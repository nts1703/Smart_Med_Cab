
import os
import argparse
import hashlib
import secrets
import pymysql
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

def connect_database():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3307")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "Med"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

def main():
    parser = argparse.ArgumentParser(
        description="Cap token cho Smart Medicine Cabinet"
    )
    parser.add_argument(
        "--device-code",
        required=True,
        help="Ma thiet bi tren Web, vi du SMC-1234ABCD"
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Huy token cu va cap token moi"
    )
    args = parser.parse_args()

    connection = connect_database()

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    device_code,
                    device_name,
                    api_token_hash
                FROM devices
                WHERE device_code = %s
                FOR UPDATE
            """, (args.device_code,))
            device = cursor.fetchone()

            if not device:
                raise ValueError(
                    "Khong tim thay ma thiet bi."
                )

            if (
                device["api_token_hash"]
                and not args.rotate
            ):
                raise ValueError(
                    "Thiet bi da co token. "
                    "Neu can cap lai, them --rotate. "
                    "Token cu se bi vo hieu hoa."
                )

            # Token ngau nhien co do dai 256 bit.
            token = secrets.token_hex(32)
            token_hash = hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest()

            cursor.execute("""
                UPDATE devices
                SET
                    api_token_hash = %s,
                    timezone = 'Asia/Ho_Chi_Minh',
                    last_sync_at = NULL
                WHERE id = %s
            """, (token_hash, device["id"]))

        connection.commit()

        print("\n" + "=" * 55)
        print("SMARTMED - CAP TOKEN THANH CONG")
        print("=" * 55)
        print("Ten thiet bi:", device["device_name"])
        print("Ma thiet bi :", device["device_code"])
        print("TOKEN       :", token)
        print("=" * 55)
        print("LUU Y:")
        print("- Luu token o noi rieng tu.")
        print("- Khong chia se token len mang.")
        print("- MySQL chi luu SHA256 cua token.")
        print("- Token cu khong con dung neu cap lai.")
        print("=" * 55 + "\n")

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    main()
