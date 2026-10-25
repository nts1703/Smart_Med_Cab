
import pymysql
from flask import current_app

def get_connection():
    cfg = current_app.config
    return pymysql.connect(
        host=cfg["DB_HOST"],
        port=cfg["DB_PORT"],
        user=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"],
        database=cfg["DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10
    )

def fetch_one(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
    finally:
        conn.close()

def fetch_all(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        conn.close()

def execute_query(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            affected = cursor.execute(sql, params or ())
            last_id = cursor.lastrowid
        conn.commit()
        return {
            "affected_rows": affected,
            "last_id": last_id
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def test_connection():
    result = fetch_one("SELECT 1 AS connected")
    return result["connected"] == 1

def get_drug_statistics():
    sql = """
        SELECT
            COUNT(*) AS total_drugs,
            COALESCE(
                SUM(
                    CASE
                        WHEN registration_status = 'unverified'
                        THEN 1 ELSE 0
                    END
                ), 0
            ) AS unverified_drugs,
            COALESCE(
                SUM(
                    CASE
                        WHEN registration_status = 'valid'
                         AND review_status = 'reviewed'
                         AND is_searchable = 1
                        THEN 1 ELSE 0
                    END
                ), 0
            ) AS verified_drugs
        FROM drug_catalog
    """
    result = fetch_one(sql)
    return {
        "total_drugs": int(result["total_drugs"] or 0),
        "unverified_drugs": int(
            result["unverified_drugs"] or 0
        ),
        "verified_drugs": int(
            result["verified_drugs"] or 0
        )
    }
