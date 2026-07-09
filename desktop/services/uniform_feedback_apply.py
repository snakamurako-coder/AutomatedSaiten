"""一律フィードバックを回答パターン単位で一括適用。"""

from __future__ import annotations

import copy
from typing import Any

from models.criteria_repo import get_answer_rows_for_pattern, save_uniform_feedback_config
from models.test_repo import get_answer_fields
from models.text_annotation_repo import get_text_annotations, save_text_annotations
from services.uniform_feedback_placement import resolve_uniform_feedback_placement
from ui_qt.floating_palette.phrase_template_prefs import (
    apply_phrase_placement_meta,
    apply_phrase_template_to_box,
    phrase_template_to_box,
)


def apply_uniform_feedback(
    test_id: str,
    field_id: str,
    answer_text: str,
    config: dict[str, Any],
    *,
    use_correction: bool,
) -> int:
    tid = str(test_id or "").strip()
    fid = str(field_id or "").strip()
    ans = str(answer_text or "").strip() or "なし"
    if not tid or not fid:
        return 0
    template = dict(config.get("template") or {})
    if not template:
        return 0

    fields = {str(f.get("id")): f for f in get_answer_fields(tid)}
    field = fields.get(fid)
    if field is None:
        return 0

    placement_h = str(config.get("placementH") or "center")
    placement_v = str(config.get("placementV") or "center")
    resolved = resolve_uniform_feedback_placement(
        field_width=float(field.get("width") or 1),
        field_height=float(field.get("height") or 1),
        box_width=float(template.get("width") or 120),
        box_height=float(template.get("height") or 36),
        placement_h=placement_h,
        placement_v=placement_v,
    )
    rect = resolved["corrected"] if use_correction else resolved["strict"]

    rows = get_answer_rows_for_pattern(tid, fid, ans)
    changed = 0
    for row in rows:
        rid = int(row.get("rowIndex") or 0)
        if rid <= 0:
            continue
        boxes = get_text_annotations(tid, rid, fid)
        filtered = [
            b
            for b in boxes
            if str(b.get("uniformFeedbackAnswer") or "") != ans
        ]
        box = phrase_template_to_box(template)
        box["x"] = float(rect["x"])
        box["y"] = float(rect["y"])
        box["width"] = float(rect["width"])
        box["height"] = float(rect["height"])
        style = dict(box.get("style") or {})
        style["textAlignH"] = str(resolved["align"]["textAlignH"])
        style["textAlignV"] = str(resolved["align"]["textAlignV"])
        box["style"] = style
        apply_phrase_template_to_box(box, template)
        apply_phrase_placement_meta(
            box,
            {
                "resultId": rid,
                "fieldId": fid,
                "studentId": row.get("studentId"),
                "studentName": row.get("studentName"),
            },
        )
        box["uniformFeedback"] = True
        box["uniformFeedbackAnswer"] = ans
        filtered.append(box)
        save_text_annotations(tid, rid, fid, filtered)
        changed += 1

    saved = {
        "phraseGroupId": str(template.get("phraseGroupId") or ""),
        "phraseTemplateId": str(template.get("id") or ""),
        "label": str(template.get("label") or ""),
        "text": str(template.get("text") or ""),
        "textHtml": str(template.get("textHtml") or ""),
        "textFormat": str(template.get("textFormat") or "plain"),
        "style": copy.deepcopy(template.get("style") or {}),
        "width": float(template.get("width") or 120),
        "height": float(template.get("height") or 36),
        "placementH": placement_h,
        "placementV": placement_v,
        "useCorrection": bool(use_correction),
    }
    save_uniform_feedback_config(tid, fid, ans, saved)
    return changed

