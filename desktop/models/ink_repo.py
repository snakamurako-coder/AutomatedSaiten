"""手書きストローク（スタイラス層）の永続化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from models.database import connect


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_ink_strokes(test_id: str, result_id: int, field_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT strokes_json FROM ink_strokes "
            "WHERE test_id = ? AND result_id = ? AND field_id = ?",
            (test_id, int(result_id), field_id),
        ).fetchone()
    if not row:
        return []
    try:
        data = json.loads(row["strokes_json"] or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def get_ink_strokes_batch(
    test_id: str, field_id: str, result_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not result_ids:
        return {}
    ids = [int(i) for i in result_ids if int(i)]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT result_id, strokes_json FROM ink_strokes "
            f"WHERE test_id = ? AND field_id = ? AND result_id IN ({placeholders})",
            (test_id, field_id, *ids),
        ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rid = int(row["result_id"])
        try:
            data = json.loads(row["strokes_json"] or "[]")
            out[rid] = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            out[rid] = []
    return out


def save_ink_strokes(
    test_id: str,
    result_id: int,
    field_id: str,
    strokes: list[dict[str, Any]],
) -> None:
    payload = json.dumps(strokes, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO ink_strokes (test_id, result_id, field_id, strokes_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(test_id, result_id, field_id) DO UPDATE SET "
            "strokes_json = excluded.strokes_json, updated_at = excluded.updated_at",
            (test_id, int(result_id), field_id, payload, _now_iso()),
        )
        conn.commit()


def collect_warped_ink_strokes(
    test_id: str,
    result_id: int,
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """記述欄ローカル座標の手書きを補正画像座標へ変換して結合。"""
    warped: list[dict[str, Any]] = []
    rid = int(result_id)
    for f in fields:
        local = get_ink_strokes(test_id, rid, f["id"])
        if not local:
            continue
        ox = float(f.get("x") or 0)
        oy = float(f.get("y") or 0)
        for stroke in local:
            pts = []
            for p in stroke.get("points") or []:
                pts.append(
                    {
                        "x": ox + float(p["x"]),
                        "y": oy + float(p["y"]),
                        "p": float(p.get("p", 1.0)),
                    }
                )
            if not pts:
                continue
            warped.append(
                {
                    "fieldId": f["id"],
                    "color": stroke.get("color") or "#111827",
                    "alpha": float(stroke.get("alpha", 1.0)),
                    "baseWidth": float(stroke.get("baseWidth") or 2.5),
                    "points": pts,
                }
            )
    return warped
