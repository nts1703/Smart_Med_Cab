
"""Smart_Med_Cab - Drug Collector v1.2.
Import Excel/CSV to MySQL Med with duplicate and conflict detection.
Compatible with existing 15-table schema.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "collector" / "data" / "raw"
OUT_DIR = ROOT / "collector" / "output"
DAV_URL = "https://dichvucong.dav.gov.vn/congbothuoc/index"
load_dotenv(ROOT / ".env")

ALIASES = {
    "drug_name": ["ten thuoc", "ten san pham", "ten thuoc thanh pham"],
    "registration_number": [
        "so dang ky", "so dang ki", "so gplh",
        "so giay phep luu hanh", "sdk", "so dk"
    ],
    "active_ingredients_text": ["hoat chat", "thanh phan", "ten hoat chat"],
    "strength": ["ham luong", "nong do", "nong do ham luong"],
    "dosage_form": ["dang bao che", "dang thuoc"],
    "manufacturer": [
        "nha san xuat", "ten cong ty san xuat",
        "ten co so san xuat", "co so san xuat"
    ],
    "manufacturer_country": ["nuoc san xuat", "quoc gia san xuat"],
    "packaging": ["quy cach dong goi", "quy cach"],
    "registration_expires_on": [
        "ngay het han", "ngay het han sdk", "ngay het hieu luc"
    ],
    "source_url": ["duong dan nguon", "url nguon", "source url", "url"],
    "therapeutic_group": ["nhom dieu tri", "nhom thuoc"]
}
FIELDS = (
    "drug_name", "registration_number", "active_ingredients_text",
    "strength", "dosage_form", "manufacturer", "manufacturer_country",
    "packaging", "registration_expires_on", "source_url"
)
CORE = ("drug_name", "active_ingredients_text", "strength", "dosage_form")
FP_FIELDS = (
    "drug_name", "active_ingredients_text", "strength",
    "dosage_form", "manufacturer", "manufacturer_country"
)
NULL_MARKERS = {
    "", "null", "none", "nan", "n/a", "na",
    "khong co", "chua ro", "-", "--"
}

def clean(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    try:
        if pd.isna(value):
            return ""
    except (ValueError, TypeError):
        pass
    s = re.sub(r"\s+", " ", str(value).replace("\ufeff", " ")).strip()
    return "" if s.lower() in NULL_MARKERS else s

def fold(value):
    s = clean(value).lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def canonical(value):
    """Normalize writing differences such as 500 mg and 500mg."""
    s = fold(value)
    return re.sub(
        r"(?<=\d)\s+(?=(?:mg|mcg|ug|g|ml|iu|ui)\b)", "", s
    )

def normalize_reg(value):
    return re.sub(r"[^A-Z0-9]", "", fold(value).upper())

def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def text_or_none(value):
    return value if clean(value) else None

def date_or_none(value):
    if not clean(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    raw = clean(value)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None

def header_map(cells):
    headers = [fold(cell) for cell in cells]
    found = {}
    for target, names in ALIASES.items():
        for index, title in enumerate(headers):
            if title in names:
                found[target] = index
                break
    return found

def find_header(rows):
    best = None
    for index, row in enumerate(rows[:30]):
        columns = header_map(row)
        if "drug_name" not in columns:
            continue
        score = (
            5 + 4 * ("registration_number" in columns)
            + 3 * ("active_ingredients_text" in columns)
            + 3 * ("dosage_form" in columns)
            + len(columns)
        )
        if not best or score > best[0]:
            best = (score, index, columns)
    return best

def sheets_from_file(path):
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        book = pd.read_excel(
            path, sheet_name=None, header=None,
            dtype=str, keep_default_na=False
        )
        return [(name, df.values.tolist()) for name, df in book.items()]
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-16", "cp1258"):
            try:
                frame = pd.read_csv(
                    path, header=None, dtype=str,
                    keep_default_na=False, sep=None,
                    engine="python", encoding=encoding
                )
                return [("CSV", frame.values.tolist())]
            except (UnicodeError, pd.errors.ParserError):
                continue
        raise ValueError("Khong doc duoc CSV. Hay luu CSV UTF-8.")
    raise ValueError("Chi ho tro XLSX / XLS / CSV.")

def source_links(sheets):
    urls = {}
    for sheet_name, rows in sheets:
        for heading, cells in enumerate(rows[:15]):
            heads = [fold(x) for x in cells]
            reg = next(
                (i for i, x in enumerate(heads)
                 if x in ALIASES["registration_number"]), None
            )
            url = next(
                (i for i, x in enumerate(heads)
                 if x.startswith(("url nguon", "duong dan nguon"))), None
            )
            if reg is None or url is None:
                continue
            for cells in rows[heading + 1:]:
                key = normalize_reg(
                    cells[reg] if len(cells) > reg else ""
                )
                value = clean(
                    cells[url] if len(cells) > url else ""
                )
                first = next(
                    (part.strip() for part in value.split(";")
                     if part.strip().startswith(("https://", "http://"))), ""
                )
                if key and first and key not in urls:
                    urls[key] = first
            print(f"[SOURCE URL] {sheet_name}: {len(urls)} registration numbers")
            break
    return urls

def read_medicines(path, limit=0):
    sheets = sheets_from_file(path)
    links = source_links(sheets)
    choices = []
    for sheet_name, rows in sheets:
        header = find_header(rows)
        if not header:
            continue
        score, heading, mapping = header
        if not (
            "active_ingredients_text" in mapping
            or "dosage_form" in mapping
        ):
            continue
        choices.append(
            (score, len(rows), sheet_name, rows, heading, mapping)
        )
    if not choices:
        raise ValueError(
            "Khong tim thay sheet: Ten thuoc + Hoat chat/Dang bao che."
        )
    _, _, sheet_name, rows, heading, mapping = max(
        choices, key=lambda x: (x[0], x[1])
    )
    print(f"[SHEET] {sheet_name} | [HEADER] dong {heading + 1}")
    print("[COLUMNS]", ", ".join(mapping))
    headers = [
        clean(v) or f"cot_{i + 1}"
        for i, v in enumerate(rows[heading])
    ]
    result = []
    for line, cells in enumerate(rows[heading + 1:], heading + 2):
        def get(field):
            j = mapping.get(field)
            return clean(cells[j]) if j is not None and j < len(cells) else ""
        name = get("drug_name")
        if not name or fold(name) in {"ten thuoc", "tong cong"}:
            continue
        row = {key: get(key) for key in FIELDS}
        row["registration_expires_on"] = date_or_none(
            row["registration_expires_on"]
        )
        reg = normalize_reg(row["registration_number"])
        if not row["source_url"]:
            row["source_url"] = links.get(reg, "")
        if row["source_url"]:
            row["source_url"] = row["source_url"].split(";")[0].strip()
        for field, size in (
            ("drug_name", 255), ("registration_number", 120),
            ("strength", 255), ("dosage_form", 255),
            ("manufacturer", 255), ("manufacturer_country", 120)
        ):
            row[field] = row[field][:size]
        raw = {
            f'{i + 1}:{headers[i] if i < len(headers) else "cot"}': clean(v)
            for i, v in enumerate(cells) if clean(v)
        }
        raw.update({
            "_file": path.name,
            "_sheet": sheet_name,
            "_excel_row": line
        })
        row["_raw_json"] = json.dumps(
            raw, ensure_ascii=False, sort_keys=True
        )
        row["_sheet"] = sheet_name
        row["_row"] = line
        row["_reg"] = reg
        # Same keys as v1.1 for backward compatibility.
        row["_external_key"] = digest(
            f"{path.name}|{sheet_name}|{line}"
        )
        identity = (
            "REG|" + reg if reg
            else "FP|" + "|".join(fold(row[f]) for f in FP_FIELDS)
        )
        row["_catalog_key"] = digest(identity)
        result.append(row)
        if limit and len(result) >= limit:
            break
    return result

def core_differences(a, b):
    return [
        field for field in CORE
        if clean(a.get(field)) and clean(b.get(field))
        and canonical(a[field]) != canonical(b[field])
    ]

def in_file_conflicts(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["_reg"]:
            groups[row["_reg"]].append(row)
    conflicts = {}
    for reg, group in groups.items():
        culprit = None
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                diff = core_differences(a, b)
                if diff:
                    culprit = (a, b, diff)
                    break
            if culprit:
                break
        if culprit:
            a, b, fields = culprit
            reason = (
                "CONFLICT_SAME_REG: same registration, different "
                + ",".join(fields)
                + f'; compare Excel rows {a["_row"]}/{b["_row"]}'
            )
            for row in group:
                conflicts[row["_external_key"]] = (
                    reason, b if row is a else a
                )
    return conflicts

def connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "Med"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15
    )

def source_code(path):
    return "XLSX_" + digest(path.name.lower())[:20].upper()

class CatalogIndex:
    def __init__(self, rows):
        self.by_reg = defaultdict(list)
        self.by_key = defaultdict(list)
        self.by_fp = defaultdict(list)
        self.by_name = defaultdict(list)
        for row in rows:
            self.add(row)

    @staticmethod
    def fingerprint(row):
        return "|".join(
            canonical(row.get(field)) for field in FP_FIELDS
        )

    def add(self, row):
        reg = normalize_reg(row.get("registration_number"))
        if reg:
            self.by_reg[reg].append(row)
        self.by_key[row["catalog_key"]].append(row)
        self.by_fp[self.fingerprint(row)].append(row)
        self.by_name[canonical(row.get("drug_name"))].append(row)

def load_catalog(cur):
    cur.execute("""
        SELECT id, catalog_key, drug_name, registration_number,
               active_ingredients_text, strength, dosage_form,
               manufacturer, manufacturer_country, packaging,
               registration_expires_on, source_url,
               registration_status, review_status, is_searchable
        FROM drug_catalog
    """)
    return CatalogIndex(cur.fetchall())

def classification(row, catalog, file_conflicts):
    key = row["_external_key"]
    if key in file_conflicts:
        reason, _ = file_conflicts[key]
        return "conflict", None, reason, {}
    reg = row["_reg"]
    if reg:
        found = catalog.by_reg.get(reg, [])
        if len({r["id"] for r in found}) > 1:
            return (
                "conflict", found[0],
                "CONFLICT_DB_REG: multiple catalog IDs use this registration", {}
            )
        if len(found) == 1:
            target = found[0]
            diff = core_differences(target, row)
            if diff:
                return (
                    "conflict", target,
                    "CONFLICT_DB_REG: different " + ",".join(diff), {}
                )
        else:
            matches = catalog.by_key.get(row["_catalog_key"], [])
            if len(matches) > 1:
                return (
                    "conflict", matches[0],
                    "CONFLICT_KEY: multiple catalog IDs", {}
                )
            target = matches[0] if matches else None
            if target and core_differences(target, row):
                return (
                    "conflict", target,
                    "CONFLICT_KEY: incompatible legacy catalog row", {}
                )
            if target is None:
                twins = [
                    r for r in catalog.by_fp.get(
                        catalog.fingerprint(row), []
                    )
                    if not normalize_reg(r.get("registration_number"))
                ]
                if twins:
                    return (
                        "review", twins[0],
                        "NEEDS_REVIEW: existing unregistered product matches this registration", {}
                    )
    else:
        known = sum(
            bool(row.get(k)) for k in (
                "active_ingredients_text", "strength",
                "dosage_form", "manufacturer"
            )
        )
        if known < 2:
            return (
                "review", None,
                "NEEDS_REVIEW: missing registration and identifying attributes", {}
            )
        fp = catalog.fingerprint(row)
        found = catalog.by_fp.get(fp, [])
        registered = [
            r for r in found
            if normalize_reg(r.get("registration_number"))
        ]
        if registered:
            return (
                "review", registered[0],
                "NEEDS_REVIEW: product matches registered record but Excel lacks number", {}
            )
        unregistered = [
            r for r in found
            if not normalize_reg(r.get("registration_number"))
        ]
        if len({r["id"] for r in unregistered}) > 1:
            return (
                "review", unregistered[0],
                "NEEDS_REVIEW: multiple similar unregistered products", {}
            )
        target = unregistered[0] if unregistered else None
        if not target and catalog.by_name.get(
            canonical(row["drug_name"])
        ):
            candidate = catalog.by_name[
                canonical(row["drug_name"])
            ][0]
            return (
                "review", candidate,
                "NEEDS_REVIEW: same name but product attributes differ/are incomplete", {}
            )
    if target is None:
        return "insert", None, "NEW_PRODUCT", {}

    noncore_conflicts = []
    for field in ("manufacturer", "manufacturer_country", "packaging"):
        if clean(row.get(field)) and clean(target.get(field)):
            if canonical(row[field]) != canonical(target[field]):
                noncore_conflicts.append(field)
    if noncore_conflicts:
        return (
            "review", target,
            "NEEDS_REVIEW: conflicting manufacturer/packaging: "
            + ",".join(noncore_conflicts), {}
        )
    fillable = (
        "registration_number", "active_ingredients_text",
        "strength", "dosage_form", "manufacturer",
        "manufacturer_country", "packaging",
        "registration_expires_on", "source_url"
    )
    missing = {
        key: row[key] for key in fillable
        if clean(row.get(key)) and not clean(target.get(key))
    }
    if missing and (
        target.get("review_status") == "reviewed"
        or target.get("registration_status") not in (
            None, "unverified"
        )
    ):
        return (
            "review", target,
            "NEEDS_REVIEW: verified record requires manual approval before enrichment", {}
        )
    return (
        "update" if missing else "duplicate",
        target,
        "FILL_ONLY_EMPTY_FIELDS" if missing else "MATCHED",
        missing
    )

def source_upsert(cur, sid, bid, row, target_id, state, message):
    cur.execute("""
        INSERT INTO drug_source_records
        (
            source_id, import_batch_id, external_key,
            record_hash, source_url, raw_json,
            matched_drug_id, processing_status, processing_note
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            import_batch_id = VALUES(import_batch_id),
            record_hash = VALUES(record_hash),
            source_url = VALUES(source_url),
            raw_json = VALUES(raw_json),
            matched_drug_id = VALUES(matched_drug_id),
            processing_status = VALUES(processing_status),
            processing_note = VALUES(processing_note),
            last_seen_at = CURRENT_TIMESTAMP
    """, (
        sid, bid, row["_external_key"],
        digest(row["_raw_json"]),
        text_or_none(row["source_url"]),
        row["_raw_json"], target_id,
        state, message[:1000]
    ))

def insert_catalog(cur, row, sid):
    cur.execute("""
        INSERT INTO drug_catalog
        (
            catalog_key, drug_name, registration_number,
            active_ingredients_text, strength, dosage_form,
            manufacturer, manufacturer_country, packaging,
            registration_expires_on, source_url, primary_source_id,
            registration_status, review_status, is_searchable
        )
        VALUES
        (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            'unverified','pending',0
        )
    """, (
        row["_catalog_key"],
        row["drug_name"],
        text_or_none(row["registration_number"]),
        text_or_none(row["active_ingredients_text"]),
        text_or_none(row["strength"]),
        text_or_none(row["dosage_form"]),
        text_or_none(row["manufacturer"]),
        text_or_none(row["manufacturer_country"]),
        text_or_none(row["packaging"]),
        row["registration_expires_on"],
        text_or_none(row["source_url"]),
        sid
    ))
    return cur.lastrowid

def reports(stem, stats, problems, decisions):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUT_DIR / f"{stem}_report.csv"
    with report.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["metric", "count"])
        writer.writerows(stats.items())
    conflicts = OUT_DIR / f"{stem}_conflicts.csv"
    fields = (
        "sheet", "excel_row", "registration_number", "drug_name",
        "strength", "active_ingredient", "dosage_form",
        "category", "reason",
        "matched_db_id", "matched_db_name",
        "matched_db_registration", "matched_db_strength",
        "matched_db_ingredient", "matched_db_form",
        "other_excel_row", "other_excel_name",
        "other_excel_strength", "other_excel_ingredient",
        "comparison_fields", "source_url"
    )
    with conflicts.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(problems)
    plan = OUT_DIR / f"{stem}_plan.csv"
    with plan.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "sheet", "excel_row", "drug_name",
            "registration_number", "decision", "matched_id", "reason"
        ])
        writer.writerows(decisions)
    print("[REPORT]", report)
    print("[CONFLICTS]", conflicts)
    print("[PLAN]", plan)

def import_file(path, limit=0, dry_run=False, offline=False, batch_size=200):
    medicines = read_medicines(path, limit)
    bad_in_file = in_file_conflicts(medicines)
    print(
        f"[INPUT] {path.name} | [ROWS] {len(medicines)} | "
        f"[IN-FILE CONFLICTS] {len(bad_in_file)}"
    )
    if not medicines:
        raise ValueError("Khong co ban ghi thuoc de xu ly.")
    if offline and not dry_run:
        raise ValueError("--offline chi dung voi --dry-run.")
    conn = None
    batch_id = None
    source_id = None
    cur = None
    if not offline:
        conn = connection()
        cur = conn.cursor()
        catalog = load_catalog(cur)
        print("[DATABASE] Loaded existing catalog for duplicate checking.")
    else:
        catalog = CatalogIndex([])
        print("[OFFLINE] Chua kiem tra trung/xung dot voi MySQL.")

    stats = {
        "read": len(medicines),
        "inserted": 0,
        "updated": 0,
        "duplicates": 0,
        "conflicts": 0,
        "needs_review": 0,
        "failed": 0
    }
    problems, decisions = [], []
    fake_id = -1
    try:
        if not dry_run:
            cur.execute("""
                INSERT IGNORE INTO drug_sources
                (
                    source_code, source_name, source_type,
                    collection_method, usage_notes
                )
                VALUES (%s,%s,'reference','excel',%s)
            """, (
                source_code(path),
                "Excel chua xac minh: " + path.name,
                "Du lieu tap hop; can doi chieu giay phep, gia han, thu hoi."
            ))
            cur.execute(
                "SELECT id FROM drug_sources WHERE source_code=%s",
                (source_code(path),)
            )
            source_id = cur.fetchone()["id"]
            batch_code = (
                "IMPORT12-"
                + datetime.now().strftime("%Y%m%d%H%M%S%f")
            )
            cur.execute("""
                INSERT INTO drug_import_batches
                (
                    source_id, batch_code, status,
                    records_fetched, records_valid
                )
                VALUES (%s,%s,'running',%s,%s)
            """, (
                source_id, batch_code,
                len(medicines), len(medicines)
            ))
            batch_id = cur.lastrowid
            conn.commit()

        for index, row in enumerate(medicines, 1):
            if not dry_run:
                cur.execute("SAVEPOINT med_v12_row")
            target = None
            try:
                action, target, reason, missing = classification(
                    row, catalog, bad_in_file
                )
                if dry_run:
                    if action == "insert":
                        shadow = {
                            **row,
                            "id": fake_id,
                            "catalog_key": row["_catalog_key"]
                        }
                        fake_id -= 1
                        catalog.add(shadow)
                    elif action == "update":
                        target.update(missing)
                        catalog.by_fp[
                            catalog.fingerprint(target)
                        ].append(target)
                else:
                    if action == "insert":
                        new_id = insert_catalog(cur, row, source_id)
                        shadow = {
                            **row,
                            "id": new_id,
                            "catalog_key": row["_catalog_key"]
                        }
                        catalog.add(shadow)
                        source_upsert(
                            cur, source_id, batch_id, row,
                            new_id, "linked", "INSERTED"
                        )
                    elif action == "update":
                        setters = ", ".join(
                            f"`{field}`=%s" for field in missing
                        )
                        cur.execute(
                            f"UPDATE drug_catalog SET {setters} WHERE id=%s",
                            tuple(missing.values()) + (target["id"],)
                        )
                        source_upsert(
                            cur, source_id, batch_id, row,
                            target["id"], "linked", reason
                        )
                        target.update(missing)
                        catalog.by_fp[
                            catalog.fingerprint(target)
                        ].append(target)
                    elif action == "duplicate":
                        source_upsert(
                            cur, source_id, batch_id, row,
                            target["id"], "linked", reason
                        )
                    else:
                        state = (
                            "error" if action == "conflict"
                            else "ignored"
                        )
                        source_upsert(
                            cur, source_id, batch_id, row,
                            None, state, reason
                        )

                counter = {
                    "insert": "inserted",
                    "update": "updated",
                    "duplicate": "duplicates",
                    "conflict": "conflicts",
                    "review": "needs_review"
                }[action]
                stats[counter] += 1
                decisions.append((
                    row["_sheet"], row["_row"],
                    row["drug_name"], row["registration_number"],
                    action, target["id"] if target else "", reason
                ))

                if action in ("conflict", "review"):
                    db_side = (
                        target if target and target.get("id", 0) > 0
                        else None
                    )
                    peer = bad_in_file.get(
                        row["_external_key"], (None, None)
                    )[1]
                    changes = (
                        ",".join(core_differences(target, row))
                        if target else ""
                    )
                    problems.append({
                        "sheet": row["_sheet"],
                        "excel_row": row["_row"],
                        "registration_number": row["registration_number"],
                        "drug_name": row["drug_name"],
                        "strength": row["strength"],
                        "active_ingredient": row["active_ingredients_text"],
                        "dosage_form": row["dosage_form"],
                        "category": action,
                        "reason": reason,
                        "matched_db_id": db_side["id"] if db_side else "",
                        "matched_db_name": db_side["drug_name"] if db_side else "",
                        "matched_db_registration": (
                            db_side["registration_number"] if db_side else ""
                        ),
                        "matched_db_strength": (
                            db_side.get("strength", "") if db_side else ""
                        ),
                        "matched_db_ingredient": (
                            db_side.get("active_ingredients_text", "")
                            if db_side else ""
                        ),
                        "matched_db_form": (
                            db_side.get("dosage_form", "") if db_side else ""
                        ),
                        "other_excel_row": (
                            peer.get("_row", "") if peer else ""
                        ),
                        "other_excel_name": (
                            peer.get("drug_name", "") if peer else ""
                        ),
                        "other_excel_strength": (
                            peer.get("strength", "") if peer else ""
                        ),
                        "other_excel_ingredient": (
                            peer.get("active_ingredients_text", "")
                            if peer else ""
                        ),
                        "comparison_fields": changes,
                        "source_url": row["source_url"]
                    })
            except Exception as exc:
                if not dry_run:
                    cur.execute("ROLLBACK TO SAVEPOINT med_v12_row")
                stats["failed"] += 1
                details = f"DB_ERROR: {str(exc)[:250]}"
                problems.append({
                    "sheet": row["_sheet"],
                    "excel_row": row["_row"],
                    "registration_number": row["registration_number"],
                    "drug_name": row["drug_name"],
                    "strength": row["strength"],
                    "active_ingredient": row["active_ingredients_text"],
                    "dosage_form": row["dosage_form"],
                    "category": "failed",
                    "reason": details,
                    "matched_db_id": "",
                    "matched_db_name": "",
                    "matched_db_registration": "",
                    "comparison_fields": "",
                    "source_url": row["source_url"]
                })
                decisions.append((
                    row["_sheet"], row["_row"],
                    row["drug_name"], row["registration_number"],
                    "failed", "", details
                ))
            if index % batch_size == 0:
                if not dry_run:
                    conn.commit()
                print(
                    f"[PROGRESS] {index}/{len(medicines)} | {stats}"
                )

        if not dry_run:
            conn.commit()
            status = (
                "partial" if (
                    stats["conflicts"]
                    + stats["needs_review"]
                    + stats["failed"]
                ) else "completed"
            )
            cur.execute("""
                UPDATE drug_import_batches
                SET finished_at=NOW(),
                    status=%s,
                    records_inserted=%s,
                    records_updated=%s,
                    records_duplicates=%s,
                    records_failed=%s
                WHERE id=%s
            """, (
                status,
                stats["inserted"],
                stats["updated"],
                stats["duplicates"],
                stats["conflicts"]
                + stats["needs_review"]
                + stats["failed"],
                batch_id
            ))
            conn.commit()
    except Exception:
        if conn:
            conn.rollback()
            if batch_id is not None:
                try:
                    cur.execute("""
                        UPDATE drug_import_batches
                        SET status='failed', finished_at=NOW()
                        WHERE id=%s
                    """, (batch_id,))
                    conn.commit()
                except Exception:
                    conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    reports(path.stem, stats, problems, decisions)
    print(
        "[DRY RUN - NO DATABASE CHANGES]"
        if dry_run else "[IMPORT COMPLETED]"
    )
    print("[DONE]", stats)
    print(
        "[NOTICE] Catalogue records remain unverified; "
        "not approved for public search."
    )

def download_dav():
    """Attempt public Excel export without bypassing website restrictions."""
    from playwright.sync_api import sync_playwright
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(accept_downloads=True)
            page.goto(
                DAV_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )
            with page.expect_download(timeout=90000) as info:
                page.get_by_text(
                    "Excel", exact=True
                ).first.click(timeout=20000)
            download = info.value
            extension = Path(
                download.suggested_filename
            ).suffix.lower()
            if extension not in (".xlsx", ".xls", ".csv"):
                raise ValueError(
                    "File tai ve khong phai Excel/CSV."
                )
            target = RAW_DIR / (
                "dav_"
                + datetime.now().strftime("%Y%m%d_%H%M%S")
                + extension
            )
            download.save_as(target)
            print("[DOWNLOAD]", target)
            return target
        except Exception as exc:
            print("[DOWNLOAD FAILED]", str(exc)[:300])
            return None
        finally:
            browser.close()

def main():
    parser = argparse.ArgumentParser(
        description="Smart Med Drug Collector v1.2"
    )
    parser.add_argument(
        "--action",
        choices=("import", "download", "all"),
        default="import"
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Only with --dry-run"
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    if args.limit < 0 or args.batch_size < 1:
        parser.error(
            "--limit >= 0 va --batch-size >= 1"
        )
    downloaded = (
        download_dav()
        if args.action in ("download", "all")
        else None
    )
    if args.action == "download":
        return
    path = args.file or downloaded
    if path is None:
        if args.action == "all":
            parser.error(
                "Tai that bai. Hay su dung --action import --file ..."
            )
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        existing = [
            p for p in RAW_DIR.iterdir()
            if p.suffix.lower() in (".xlsx", ".xls", ".csv")
        ]
        if not existing:
            parser.error(
                "Khong co file trong collector/data/raw/. "
                "Hay dung --file."
            )
        path = max(
            existing,
            key=lambda p: p.stat().st_mtime
        )
    if not path.is_file():
        parser.error(
            "Khong tim thay: " + str(path)
        )
    import_file(
        path, args.limit, args.dry_run,
        args.offline, args.batch_size
    )

if __name__ == "__main__":
    main()
