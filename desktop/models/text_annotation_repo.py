"""テキスト注釈（テキストボックス）の永続化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from models.database import connect

TEXT_PALETTE_COLORS: tuple[str, ...] = (
    "#111827",
    "#dc2626",
    "#2563eb",
    "#16a34a",
    "#ea580c",
    "#9333ea",
)

DEFAULT_TEXT_STYLE: dict[str, Any] = {
    "textColor": TEXT_PALETTE_COLORS[0],
    "fontSize": 14,
    "fontFamily": "meiryo.ttc",
    "templateId": "A",
    "fillAlpha": 0.0,
    "borderWidth": 0,
    "borderAlpha": 0.0,
}

TEXT_STYLE_TEMPLATE_A: dict[str, Any] = {
    "templateId": "A",
    "textColor": TEXT_PALETTE_COLORS[0],
    "fillAlpha": 0.0,
    "borderWidth": 0,
    "borderAlpha": 0.0,
}

TEXT_STYLE_TEMPLATE_B: dict[str, Any] = {
    "templateId": "B",
    "textColor": TEXT_PALETTE_COLORS[0],
    "fillAlpha": 0.2,
    "borderWidth": 0,
    "borderAlpha": 0.0,
}

TEXT_STYLE_TEMPLATES: dict[str, dict[str, Any]] = {
    "A": TEXT_STYLE_TEMPLATE_A,
    "B": TEXT_STYLE_TEMPLATE_B,
}


def complementary_hex(hex_color: str) -> str:
    """RGB 補色 (#rrggbb)。"""
    h = str(hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return "#ffffff"
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"#{255 - r:02x}{255 - g:02x}{255 - b:02x}"


def resolve_text_style(style: dict[str, Any] | None) -> dict[str, Any]:
    """templateId に応じて fillColor / borderColor 等を確定する。"""
    merged = dict(DEFAULT_TEXT_STYLE)
    if isinstance(style, dict):
        merged.update(style)
    tid = str(merged.get("templateId") or "").upper()
    tc = str(merged.get("textColor") or TEXT_PALETTE_COLORS[0])
    if tid == "A":
        merged["borderWidth"] = 0
        merged["borderAlpha"] = 0.0
        merged["fillAlpha"] = 0.0
        merged["textColor"] = tc
    elif tid == "B":
        merged["textColor"] = tc
        merged["fillColor"] = complementary_hex(tc)
        merged["borderColor"] = tc
        merged["borderWidth"] = 0
        merged["borderAlpha"] = 0.0
        merged["fillAlpha"] = 0.2
    return merged


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_text_box(x: float, y: float) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "x": float(x),
        "y": float(y),
        "width": 32.0,
        "height": 18.0,
        "text": "",
        "style": dict(TEXT_STYLE_TEMPLATE_A),
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
    ids = [int(i) for i in result_ids]
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
