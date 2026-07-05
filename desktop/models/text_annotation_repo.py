"""テキスト注釈（テキストボックス）の永続化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from models.database import connect

DEFAULT_TEXT_STYLE: dict[str, Any] = {
    "borderColor": "#2563eb",
    "borderWidth": 2,
    "borderAlpha": 1.0,
    "fillColor": "#ffffff",
    "fillAlpha": 0.85,
    "textColor": "#111827",
    "fontSize": 14,
    "fontFamily": "meiryo.ttc",
    "bold": False,
    "underline": False,
    "vertical": False,
    "align": "left",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_text_box(x: float, y: float, *, width: float = 120.0, height: float = 36.0) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
        "text": "",
        "style": dict(DEFAULT_TEXT_STYLE),
    }


def get_text_annotations(test_id: str, result_id: int, field_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT annotations_json FROM text_annotations "
            "WHERE test_id = ? AND result_id = ? AND field_id = ?",
            (test_id, int(result_id), field_id),
        ).fetchone()
    if not row:
        return []
    try:
        data = json.loads(row["annotations_json"] or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def get_text_annotations_batch(
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
            f"SELECT result_id, annotations_json FROM text_annotations "
            f"WHERE test_id = ? AND field_id = ? AND result_id IN ({placeholders})",
            (test_id, field_id, *ids),
        ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rid = int(row["result_id"])
        try:
            data = json.loads(row["annotations_json"] or "[]")
            out[rid] = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            out[rid] = []
    return out


def save_text_annotations(
    test_id: str,
    result_id: int,
    field_id: str,
    annotations: list[dict[str, Any]],
) -> None:
    payload = json.dumps(annotations, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO text_annotations "
            "(test_id, result_id, field_id, annotations_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(test_id, result_id, field_id) DO UPDATE SET "
            "annotations_json = excluded.annotations_json, updated_at = excluded.updated_at",
            (test_id, int(result_id), field_id, payload, _now_iso()),
        )
        conn.commit()


def collect_warped_text_annotations(
    test_id: str,
    result_id: int,
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """記述欄ローカル座標のテキストを補正画像座標へ変換。"""
    warped: list[dict[str, Any]] = []
    rid = int(result_id)
    for f in fields:
        local = get_text_annotations(test_id, rid, f["id"])
        if not local:
            continue
        ox = float(f.get("x") or 0)
        oy = float(f.get("y") or 0)
        for box in local:
            warped.append(
                {
                    **box,
                    "fieldId": f["id"],
                    "x": ox + float(box.get("x") or 0),
                    "y": oy + float(box.get("y") or 0),
                }
            )
    return warped
