"""⑧ 本人確認欄・⑨ 照合データ。"""

from __future__ import annotations

from typing import Any

from models.database import connect, init_db
from models.test_repo import touch_progress_conn

IDENTITY_TYPES = ["学年", "組", "番号", "ID", "氏名"]
IDENTITY_BASIS_WARPED = "warped"
IDENTITY_BASIS_ORIGINAL = "original"
_IDENTITY_BASIS_KEY = "本人欄座標基準"


def get_identity_coord_basis(test_id: str) -> str | None:
    """本人欄の座標がどの画像上か。未選択かつ未保存なら None。

    保存済みで基準が無い古いデータは、従来どおり補正画像とする。
    """
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM test_info WHERE test_id = ? AND key = ?",
            (test_id, _IDENTITY_BASIS_KEY),
        ).fetchone()
        stored = str(row["value"] if row else "").strip()
        if stored in (IDENTITY_BASIS_WARPED, IDENTITY_BASIS_ORIGINAL):
            return stored
        has_fields = conn.execute(
            "SELECT 1 FROM identity_fields WHERE test_id = ? LIMIT 1",
            (test_id,),
        ).fetchone()
    if has_fields:
        return IDENTITY_BASIS_WARPED
    return None


def set_identity_coord_basis(test_id: str, basis: str) -> None:
    if basis not in (IDENTITY_BASIS_WARPED, IDENTITY_BASIS_ORIGINAL):
        raise ValueError("座標の基準は補正画像か元画像です。")
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO test_info(test_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(test_id, key) DO UPDATE SET value = excluded.value
            """,
            (test_id, _IDENTITY_BASIS_KEY, basis),
        )
        conn.commit()


def clear_identity_fields(test_id: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM identity_fields WHERE test_id = ?", (test_id,))
        conn.commit()


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
