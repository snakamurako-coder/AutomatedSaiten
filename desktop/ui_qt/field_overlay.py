"""②で設定した記述欄枠をプレビュー上に重ね描画する。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ui_qt.style import COLORS


def draw_answer_fields(
    painter: QPainter,
    fields: list[dict[str, Any]] | None,
    *,
    scale: float,
    color: str | None = None,
    pen_width: int = 2,
    highlight_id: str = "",
    highlight_color: str = "#f59e0b",
) -> None:
    """画像ピクセル座標の記述欄を、表示スケールで矩形描画する。"""
    if not fields or scale <= 0:
        return
    base = color or COLORS["accent"]
    for f in fields:
        x = int(float(f.get("x") or 0) * scale)
        y = int(float(f.get("y") or 0) * scale)
        w = int(float(f.get("width") or 0) * scale)
        h = int(float(f.get("height") or 0) * scale)
        if w <= 0 or h <= 0:
            continue
        fid = str(f.get("id") or "")
        hot = bool(highlight_id) and fid == highlight_id
        pen = QPen(QColor(highlight_color if hot else base))
        pen.setWidth(3 if hot else pen_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(x, y, max(1, w), max(1, h))
