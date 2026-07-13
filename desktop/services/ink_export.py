"""個票エクスポート用の手書きストローク間引き（永続データは変更しない）。"""

from __future__ import annotations

from typing import Any

# 隣接点の最小距離 ≈ baseWidth * この係数（端点は必ず残す）
_MIN_SEG_FACTOR = 0.35


def thin_stroke_points(
    points: list[dict[str, Any]],
    *,
    base_width: float = 2.5,
) -> list[dict[str, Any]]:
    """近接点を間引き、端点は必ず残す。"""
    if len(points) < 3:
        return list(points)
    min_dist = max(0.5, float(base_width) * _MIN_SEG_FACTOR)
    min_dist_sq = min_dist * min_dist
    out: list[dict[str, Any]] = [points[0]]
    lx = float(points[0]["x"])
    ly = float(points[0]["y"])
    for p in points[1:-1]:
        x = float(p["x"])
        y = float(p["y"])
        dx = x - lx
        dy = y - ly
        if dx * dx + dy * dy < min_dist_sq:
            continue
        out.append(p)
        lx, ly = x, y
    out.append(points[-1])
    return out


def thin_ink_strokes_for_export(strokes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """エクスポート直前のコピーに点間引きを適用する。"""
    thinned: list[dict[str, Any]] = []
    for stroke in strokes or []:
        pts = stroke.get("points") or []
        if not pts:
            continue
        base_w = float(stroke.get("baseWidth") or 2.5)
        new_pts = thin_stroke_points(pts, base_width=base_w)
        item = dict(stroke)
        item["points"] = new_pts
        thinned.append(item)
    return thinned
