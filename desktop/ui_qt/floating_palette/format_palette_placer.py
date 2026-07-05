"""書式パレットの自動配置。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect


def place_format_palette(
    box_global: QRect,
    palette_size: tuple[int, int],
    *,
    viewer_global: QRect | None = None,
    pinned_pos: QPoint | None = None,
) -> QPoint:
    """テキストボックス近傍に書式パレットを配置（ピン留め時は固定位置）。"""
    if pinned_pos is not None:
        return pinned_pos
    pw, ph = palette_size
    margin = 8
    gap = 12
    # 右側
    x = box_global.right() + gap
    y = box_global.top()
    if viewer_global is not None:
        if x + pw > viewer_global.right() - margin:
            x = box_global.left() - gap - pw
        if x < viewer_global.left() + margin:
            x = max(viewer_global.left() + margin, box_global.center().x() - pw // 2)
        if y + ph > viewer_global.bottom() - margin:
            y = viewer_global.bottom() - margin - ph
        if y < viewer_global.top() + margin:
            y = viewer_global.top() + margin
    return QPoint(int(x), int(y))


def clamp_window_to_viewer(
    window_rect: QRect,
    viewer_global: QRect,
    *,
    margin: int = 4,
) -> QPoint:
    """ウィンドウ左上座標をビューア内にクランプ。"""
    x = window_rect.x()
    y = window_rect.y()
    w = window_rect.width()
    h = window_rect.height()
    min_x = viewer_global.left() + margin
    min_y = viewer_global.top() + margin
    max_x = viewer_global.right() - margin - w
    max_y = viewer_global.bottom() - margin - h
    if max_x < min_x:
        x = min_x
    else:
        x = max(min_x, min(x, max_x))
    if max_y < min_y:
        y = min_y
    else:
        y = max(min_y, min(y, max_y))
    return QPoint(int(x), int(y))
