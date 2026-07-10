"""一律フィードバック用の九分割配置ヘルパー。"""

from __future__ import annotations

from typing import Any


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _rect_for_placement(
    field_w: float,
    field_h: float,
    box_w: float,
    box_h: float,
    placement_h: str,
    placement_v: str,
) -> tuple[float, float, float, float]:
    """記述欄の端にテキストボックスの辺をぴったり揃えて配置する。"""
    ph = str(placement_h or "center").lower()
    pv = str(placement_v or "center").lower()
    if ph == "left":
        x = 0.0
    elif ph == "right":
        x = field_w - box_w
    else:
        x = (field_w - box_w) / 2.0

    if pv == "top":
        y = 0.0
    elif pv == "bottom":
        y = field_h - box_h
    else:
        y = (field_h - box_h) / 2.0

    return x, y, box_w, box_h


def resolve_uniform_feedback_placement(
    *,
    field_width: float,
    field_height: float,
    box_width: float,
    box_height: float,
    placement_h: str,
    placement_v: str,
) -> dict[str, Any]:
    fw = max(1.0, float(field_width or 1.0))
    fh = max(1.0, float(field_height or 1.0))
    bw = max(24.0, float(box_width or 120.0))
    bh = max(16.0, float(box_height or 36.0))
    align_h = str(placement_h or "center").lower()
    align_v = str(placement_v or "center").lower()
    if align_h not in {"left", "center", "right"}:
        align_h = "center"
    if align_v not in {"top", "center", "bottom"}:
        align_v = "center"

    sx, sy, sw, sh = _rect_for_placement(fw, fh, bw, bh, align_h, align_v)

    cw = min(sw, fw)
    ch = min(sh, fh)
    cx = _clamp(sx, 0.0, max(0.0, fw - cw))
    cy = _clamp(sy, 0.0, max(0.0, fh - ch))

    needs = (
        abs(cx - sx) > 0.01
        or abs(cy - sy) > 0.01
        or abs(cw - sw) > 0.01
        or abs(ch - sh) > 0.01
    )
    overflow: list[str] = []
    if sx < 0:
        overflow.append("左")
    if sy < 0:
        overflow.append("上")
    if sx + sw > fw:
        overflow.append("右")
    if sy + sh > fh:
        overflow.append("下")

    return {
        "strict": {"x": sx, "y": sy, "width": sw, "height": sh},
        "corrected": {"x": cx, "y": cy, "width": cw, "height": ch},
        "needsCorrection": bool(needs),
        "overflowDirections": overflow,
        "align": {"textAlignH": align_h, "textAlignV": align_v},
    }
