
from services.database import fetch_all

def search_catalog(keyword="", page=1, page_size=15):
    keyword = str(keyword or "").strip()[:100]
    page = max(1, min(int(page), 10000))
    page_size = max(1, min(int(page_size), 30))
    offset = (page - 1) * page_size
    params = []
    where = ""
    if keyword:
        where = """
            WHERE drug_name LIKE %s
               OR active_ingredients_text LIKE %s
               OR registration_number LIKE %s
        """
        pattern = f"%{keyword}%"
        params = [pattern, pattern, pattern]
    sql = f"""
        SELECT id, drug_name, registration_number,
               active_ingredients_text, strength,
               dosage_form, manufacturer,
               registration_status, review_status,
               is_searchable
        FROM drug_catalog
        {where}
        ORDER BY drug_name ASC, id ASC
        LIMIT %s OFFSET %s
    """
    rows = fetch_all(sql, tuple(params + [page_size + 1, offset]))
    has_more = len(rows) > page_size
    items = []
    for row in rows[:page_size]:
        verified = (
            row["registration_status"] == "valid"
            and row["review_status"] == "reviewed"
            and int(row["is_searchable"] or 0) == 1
        )
        items.append({
            "id": int(row["id"]),
            "name": row["drug_name"],
            "registration_number": row["registration_number"] or "",
            "active_ingredient": row["active_ingredients_text"] or "",
            "strength": row["strength"] or "",
            "dosage_form": row["dosage_form"] or "",
            "manufacturer": row["manufacturer"] or "",
            "verified": verified,
            "status_label": (
                "Đã đối chiếu trong hệ thống"
                if verified else "Chưa xác minh"
            )
        })
    return {
        "items": items,
        "page": page,
        "has_more": has_more
    }
