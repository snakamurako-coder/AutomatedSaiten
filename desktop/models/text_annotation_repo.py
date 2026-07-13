"""テキスト注釈（テキストボックス）の永続化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from models.database import connect
from models.ink_repo import SHEET_FIELD_ID, is_sheet_field_id

TEXT_PALETTE_COLORS: tuple[str, ...] = (
    "#111827",
    "#dc2626",
    "#2563eb",
    "#16a34a",
    "#ea580c",
    "#9333ea",
)

# 新規テキストボックスの内蔵既定: 赤字・14pt・行間20・左/上寄せ・背景なし
# （詳細設定の描画ツールで上書き可能）
DEFAULT_TEXT_COLOR = "#dc2626"

DEFAULT_TEXT_STYLE: dict[str, Any] = {
    "textColor": DEFAULT_TEXT_COLOR,
    "fontSize": 14,
    "lineSpacing": 20,
    "fontFamily": "meiryo.ttc",
    "templateId": "A",
    "fillAlpha": 0.0,
    "borderWidth": 0,
    "borderAlpha": 0.0,
    "textAlignH": "left",
    "textAlignV": "top",
}

TEXT_STYLE_TEMPLATE_A: dict[str, Any] = {
    "templateId": "A",
    "textColor": DEFAULT_TEXT_COLOR,
    "fontSize": 14,
    "lineSpacing": 20,
    "fillAlpha": 0.0,
    "borderWidth": 0,
    "borderAlpha": 0.0,
    "textAlignH": "left",
    "textAlignV": "top",
}

TEXT_STYLE_TEMPLATE_B: dict[str, Any] = {
    "templateId": "B",
    "textColor": DEFAULT_TEXT_COLOR,
    "fontSize": 14,
    "lineSpacing": 20,
    "fillAlpha": 0.2,
    "borderWidth": 0,
    "borderAlpha": 0.0,
    "textAlignH": "left",
    "textAlignV": "top",
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
    tc = str(merged.get("textColor") or DEFAULT_TEXT_COLOR)
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
    h = str(merged.get("textAlignH") or "left").lower()
    if h not in ("left", "center", "right"):
        h = "left"
    merged["textAlignH"] = h
    v = str(merged.get("textAlignV") or "top").lower()
    if v not in ("top", "center", "bottom"):
        v = "top"
    merged["textAlignV"] = v
    return merged


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_text_box(
    x: float,
    y: float,
    *,
    width: float = 120.0,
    height: float = 36.0,
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
        "text": "",
        "style": resolve_text_style(
            style if isinstance(style, dict) else dict(TEXT_STYLE_TEMPLATE_A)
        ),
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
    """記述欄ローカル座標のテキストを補正画像座標へ変換。

    末尾に答案全体レイヤー（__sheet__）を追記する。
    """
    warped: list[dict[str, Any]] = []
    rid = int(result_id)
    for f in fields:
        fid = str(f.get("id") or "")
        if not fid or is_sheet_field_id(fid):
            continue
        local = get_text_annotations(test_id, rid, fid)
        if not local:
            continue
        ox = float(f.get("x") or 0)
        oy = float(f.get("y") or 0)
        for box in local:
            warped.append(
                {
                    **box,
                    "fieldId": fid,
                    "x": ox + float(box.get("x") or 0),
                    "y": oy + float(box.get("y") or 0),
                }
            )
    for box in get_text_annotations(test_id, rid, SHEET_FIELD_ID):
        item = dict(box)
        item["fieldId"] = SHEET_FIELD_ID
        item["source"] = "sheet"
        warped.append(item)
    return warped


def _aabb_intersects(
    ax: float, ay: float, aw: float, ah: float,
    bx: float, by: float, bw: float, bh: float,
) -> bool:
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def project_sheet_text_to_field_local(
    sheet_boxes: list[dict[str, Any]],
    field: dict[str, Any],
) -> list[dict[str, Any]]:
    """シートTBのうち記述欄と交差するものをローカル座標へ写す。

    source='sheet' を付与。クロップでは削除のみ可。
    """
    ox = float(field.get("x") or 0)
    oy = float(field.get("y") or 0)
    fw = float(field.get("width") or 0)
    fh = float(field.get("height") or 0)
    out: list[dict[str, Any]] = []
    for box in sheet_boxes or []:
        bx = float(box.get("x") or 0)
        by = float(box.get("y") or 0)
        bw = float(box.get("width") or 0)
        bh = float(box.get("height") or 0)
        if not _aabb_intersects(bx, by, bw, bh, ox, oy, fw, fh):
            continue
        item = dict(box)
        item["x"] = bx - ox
        item["y"] = by - oy
        item["source"] = "sheet"
        item["fieldId"] = SHEET_FIELD_ID
        out.append(item)
    return out


def field_local_text_to_warped(
    local_boxes: list[dict[str, Any]],
    field: dict[str, Any],
) -> list[dict[str, Any]]:
    """欄ローカルTBを補正画像座標へ（全容下敷き用）。"""
    ox = float(field.get("x") or 0)
    oy = float(field.get("y") or 0)
    fid = str(field.get("id") or "")
    out: list[dict[str, Any]] = []
    for box in local_boxes or []:
        if str(box.get("source") or "") == "sheet":
            continue
        item = dict(box)
        item["fieldId"] = fid
        item["x"] = ox + float(box.get("x") or 0)
        item["y"] = oy + float(box.get("y") or 0)
        out.append(item)
    return out


def strip_sheet_source_for_save(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """永続化用に source=sheet の項目を除き、ランタイムフラグを落とす。"""
    out: list[dict[str, Any]] = []
    for box in annotations or []:
        if str(box.get("source") or "") == "sheet":
            continue
        item = dict(box)
        item.pop("source", None)
        out.append(item)
    return out


def sheet_boxes_without_ids(
    sheet_boxes: list[dict[str, Any]], remove_ids: set[str]
) -> list[dict[str, Any]]:
    """シートTB一覧から指定 id を除いたコピー。"""
    rid = {str(i) for i in remove_ids}
    out: list[dict[str, Any]] = []
    for box in sheet_boxes or []:
        if str(box.get("id") or "") in rid:
            continue
        item = dict(box)
        item.pop("source", None)
        out.append(item)
    return out


def iter_text_annotations_for_test(
    test_id: str,
) -> list[tuple[int, str, list[dict[str, Any]]]]:
    """テスト内の全 text_annotations 行を (result_id, field_id, boxes) で返す。"""
    tid = str(test_id or "").strip()
    if not tid:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT result_id, field_id, annotations_json FROM text_annotations "
            "WHERE test_id = ?",
            (tid,),
        ).fetchall()
    out: list[tuple[int, str, list[dict[str, Any]]]] = []
    for row in rows:
        try:
            data = json.loads(row["annotations_json"] or "[]")
            boxes = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            boxes = []
        out.append((int(row["result_id"]), str(row["field_id"]), boxes))
    return out


def find_phrase_placements(test_id: str, phrase_group_id: str) -> list[dict[str, Any]]:
    """同一 phraseGroupId の配置一覧（表示用メタデータ付き）。"""
    from models.test_repo import get_all_results, get_answer_fields

    gid = str(phrase_group_id or "").strip()
    if not gid:
        return []
    field_names = {
        str(f["id"]): str(f.get("displayName") or f["id"])
        for f in get_answer_fields(test_id)
    }
    results_by_id = {int(r["id"]): r for r in get_all_results(test_id)}
    placements: list[dict[str, Any]] = []
    for result_id, field_id, boxes in iter_text_annotations_for_test(test_id):
        for box in boxes:
            if str(box.get("phraseGroupId") or "") != gid:
                continue
            row = results_by_id.get(int(result_id), {})
            placements.append(
                {
                    "resultId": int(result_id),
                    "fieldId": field_id,
                    "fieldName": field_names.get(field_id, field_id),
                    "boxId": str(box.get("id") or ""),
                    "studentId": str(
                        box.get("placedStudentId") or row.get("studentId") or ""
                    ),
                    "studentName": str(
                        box.get("placedStudentName") or row.get("name") or ""
                    ),
                    "fileName": str(row.get("fileName") or ""),
                    "box": box,
                }
            )
    return placements


def bulk_update_phrase_boxes(
    test_id: str,
    phrase_group_id: str,
    operation: str,
    **params: Any,
) -> int:
    """同一 phraseGroupId のテキストボックスを一括更新。変更件数を返す。"""
    from ui_qt.floating_palette.text_rich import (
        append_text_to_box,
        replace_box_from_template,
        replace_box_text,
    )

    gid = str(phrase_group_id or "").strip()
    op = str(operation or "").strip().lower()
    if not gid or not op:
        return 0
    changed = 0
    for result_id, field_id, boxes in iter_text_annotations_for_test(test_id):
        if not boxes:
            continue
        row_changed = False
        new_boxes: list[dict[str, Any]] = []
        for box in boxes:
            if str(box.get("phraseGroupId") or "") != gid:
                new_boxes.append(box)
                continue
            if op == "delete":
                row_changed = True
                changed += 1
                continue
            if op == "append":
                append_text_to_box(
                    box,
                    str(params.get("text") or ""),
                    position=str(params.get("position") or "after"),
                )
                row_changed = True
                changed += 1
                new_boxes.append(box)
                continue
            if op == "replace":
                tpl = params.get("template")
                if isinstance(tpl, dict):
                    replace_box_from_template(box, tpl)
                else:
                    replace_box_text(
                        box,
                        str(params.get("text") or ""),
                        text_html=params.get("textHtml"),
                        text_format=params.get("textFormat"),
                    )
                row_changed = True
                changed += 1
                new_boxes.append(box)
                continue
            new_boxes.append(box)
        if row_changed:
            save_text_annotations(test_id, result_id, field_id, new_boxes)
    return changed
