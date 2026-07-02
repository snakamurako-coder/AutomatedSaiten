"""⑧ 本人確認欄・⑨ 照合データ。"""

from __future__ import annotations

from typing import Any

from models.database import connect, init_db
from models.test_repo import touch_progress_conn

IDENTITY_TYPES = ["学年", "組", "番号", "ID", "氏名"]


def get_identity_fields(test_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT field_type, x, y, width, height FROM identity_fields WHERE test_id = ?",
            (test_id,),
        ).fetchall()
    fields = [
        {
            "type": r["field_type"],
            "x": r["x"],
            "y": r["y"],
            "width": r["width"],
            "height": r["height"],
        }
        for r in rows
    ]
    order = {t: i for i, t in enumerate(IDENTITY_TYPES)}
    fields.sort(key=lambda f: order.get(f["type"], 99))
    return fields


def save_identity_fields(test_id: str, fields: list[dict[str, Any]]) -> int:
    if not fields:
        raise ValueError("本人確認欄を 1 つ以上設定してください。")
    for f in fields:
        t = str(f.get("type") or "").strip()
        if t not in IDENTITY_TYPES:
            raise ValueError(f"不正な欄種別です: {t}")
    with connect() as conn:
        conn.execute("DELETE FROM identity_fields WHERE test_id = ?", (test_id,))
        for f in fields:
            conn.execute(
                """
                INSERT OR REPLACE INTO identity_fields(test_id, field_type, x, y, width, height)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    str(f["type"]),
                    int(f.get("x") or 0),
                    int(f.get("y") or 0),
                    int(f.get("width") or 0),
                    int(f.get("height") or 0),
                ),
            )
        touch_progress_conn(conn, test_id, 8)
        conn.commit()
    return len(fields)


def get_verification_data(test_id: str) -> dict[str, Any]:
    """⑨ 照合用: 結果行 + 本人確認欄。"""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, student_id, name, file_name, warped_path, source_path
            FROM results WHERE test_id = ? ORDER BY file_name
            """,
            (test_id,),
        ).fetchall()
    return {
        "rows": [
            {
                "id": r["id"],
                "studentId": r["student_id"] or "",
                "name": r["name"] or "",
                "fileName": r["file_name"],
                "warpedPath": r["warped_path"] or "",
                "sourcePath": r["source_path"] or "",
            }
            for r in rows
        ],
        "identityFields": get_identity_fields(test_id),
    }
