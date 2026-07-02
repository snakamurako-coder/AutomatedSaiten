"""模範解答画像上の記述欄矩形エディタ（Qt 版）。

QPainter はネイティブにアルファ合成できるため、画面は Qt が直接描画する。
塗り色・透明度は services.compositor と同じ定数を使い、印刷出力と見た目を揃える。
"""

from __future__ import annotations

import copy
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QScrollArea, QWidget

from services.compositor import (
    REGION_FILL_ALPHA,
    REGION_FILL_ALPHA_SELECTED,
    REGION_STROKE_NORMAL,
    REGION_STROKE_SELECTED,
)
from services.image_loader import load_image_bgr
from ui_qt.helpers import bgr_to_qpixmap
from ui_qt.style import COLORS

_CURSORS = {
    "nw": Qt.SizeFDiagCursor,
    "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor,
    "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor,
    "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor,
    "w": Qt.SizeHorCursor,
}


def _fill_color(hex_color: str, alpha: float) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


class _EditorCanvas(QWidget):
    HANDLE = 8
    MIN_SIZE = 15

    changed = Signal()
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.regions: list[dict[str, Any]] = []
        self.selected_idx = -1
        self._drag: dict[str, Any] | None = None
        self._image_bgr: np.ndarray | None = None
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        # ラベル指定モード: 新規矩形の ID をこのラベルにし、同ラベルの既存矩形を置き換える
        # （⑧本人欄=欄種別、⑩出力欄=slotKey で使用）
        self.pending_label: str | None = None
        self.replace_same_label = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(480, 360)

    # --- 画像 ---

    def set_image(self, image_bgr: np.ndarray) -> None:
        self._image_bgr = image_bgr.copy()
        self._pixmap = bgr_to_qpixmap(self._image_bgr)
        self._update_scale()
        self.update()

    def _update_scale(self) -> None:
        if self._pixmap is None:
            return
        # 高 DPI でも等倍以下で全体が見えるよう、親ビューポート幅に合わせる
        parent = self.parentWidget()
        avail_w = max(320, (parent.width() - 24) if parent else 760)
        self._scale = min(1.0, avail_w / self._pixmap.width())
        w = int(self._pixmap.width() * self._scale)
        h = int(self._pixmap.height() * self._scale)
        self.setFixedSize(w, h)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)

    # --- 座標変換 ---

    def _to_image(self, pos: QPointF) -> tuple[float, float]:
        return pos.x() / self._scale, pos.y() / self._scale

    def _region_rect_disp(self, r: dict[str, Any]) -> QRectF:
        return QRectF(
            r["x"] * self._scale,
            r["y"] * self._scale,
            r["w"] * self._scale,
            r["h"] * self._scale,
        )

    # --- 描画 ---

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self._pixmap is None:
            painter.fillRect(self.rect(), QColor(COLORS["surface"]))
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "PDF / JPG / PNG をドロップ\nまたは「画像を開く」",
            )
            return

        target = QRectF(0, 0, self.width(), self.height())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)

        for idx, r in enumerate(self.regions):
            rect = self._region_rect_disp(r)
            selected = idx == self.selected_idx
            stroke = REGION_STROKE_SELECTED if selected else REGION_STROKE_NORMAL
            alpha = REGION_FILL_ALPHA_SELECTED if selected else REGION_FILL_ALPHA
            painter.fillRect(rect, _fill_color(stroke, alpha))
            pen = QPen(QColor(stroke))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.drawText(
                rect.adjusted(5, 3, -3, -3),
                Qt.AlignTop | Qt.AlignLeft,
                r.get("displayName") or r["id"],
            )
            if selected:
                self._paint_handles(painter, rect)

        if self._drag and self._drag["type"] == "create":
            x0 = self._drag["start_x"] * self._scale
            y0 = self._drag["start_y"] * self._scale
            x1 = self._drag.get("cur_x", self._drag["start_x"]) * self._scale
            y1 = self._drag.get("cur_y", self._drag["start_y"]) * self._scale
            pen = QPen(QColor(COLORS["accent"]))
            pen.setWidth(2)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(_fill_color(COLORS["accent"], 0.08))
            painter.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())

    def _paint_handles(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["accent"]))
        hs = self.HANDLE
        for px, py in self._handle_points(rect):
            painter.drawRect(QRectF(px - hs / 2, py - hs / 2, hs, hs))

    @staticmethod
    def _handle_points(rect: QRectF) -> list[tuple[float, float]]:
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        return [
            (x, y),
            (x + w / 2, y),
            (x + w, y),
            (x + w, y + h / 2),
            (x + w, y + h),
            (x + w / 2, y + h),
            (x, y + h),
            (x, y + h / 2),
        ]

    # --- ヒットテスト ---

    def _hit_handle(self, r: dict[str, Any], px: float, py: float) -> str | None:
        tol = self.HANDLE / self._scale
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        handles = [
            ("nw", x, y),
            ("n", x + w / 2, y),
            ("ne", x + w, y),
            ("e", x + w, y + h / 2),
            ("se", x + w, y + h),
            ("s", x + w / 2, y + h),
            ("sw", x, y + h),
            ("w", x, y + h / 2),
        ]
        for key, hx, hy in handles:
            if abs(hx - px) <= tol and abs(hy - py) <= tol:
                return key
        return None

    def _hit_region(self, px: float, py: float) -> int:
        for i in range(len(self.regions) - 1, -1, -1):
            r = self.regions[i]
            if r["x"] <= px <= r["x"] + r["w"] and r["y"] <= py <= r["y"] + r["h"]:
                return i
        return -1

    # --- マウス ---

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        if self._image_bgr is None:
            self.status.emit("先に PDF / JPG / PNG の模範解答を読み込んでください")
            return
        px, py = self._to_image(event.position())
        if self.selected_idx >= 0:
            handle = self._hit_handle(self.regions[self.selected_idx], px, py)
            if handle:
                self._drag = {
                    "type": "resize",
                    "handle": handle,
                    "start_x": px,
                    "start_y": py,
                    "orig": copy.deepcopy(self.regions[self.selected_idx]),
                }
                return
        idx = self._hit_region(px, py)
        if idx >= 0:
            self.selected_idx = idx
            self._drag = {
                "type": "move",
                "start_x": px,
                "start_y": py,
                "orig": copy.deepcopy(self.regions[idx]),
            }
            self.update()
            self.changed.emit()
            return
        self.selected_idx = -1
        self._drag = {"type": "create", "start_x": px, "start_y": py}
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        px, py = self._to_image(event.position())
        if self._drag:
            kind = self._drag["type"]
            if kind == "create":
                self._drag["cur_x"] = px
                self._drag["cur_y"] = py
            elif kind == "move":
                orig = self._drag["orig"]
                r = self.regions[self.selected_idx]
                r["x"] = orig["x"] + (px - self._drag["start_x"])
                r["y"] = orig["y"] + (py - self._drag["start_y"])
            elif kind == "resize":
                self._apply_resize(px, py)
            self.update()
            return
        self._update_hover_cursor(px, py)

    def _update_hover_cursor(self, px: float, py: float) -> None:
        if self._image_bgr is None:
            return
        if self.selected_idx >= 0:
            handle = self._hit_handle(self.regions[self.selected_idx], px, py)
            if handle:
                self.setCursor(_CURSORS.get(handle, Qt.CrossCursor))
                return
        self.setCursor(Qt.SizeAllCursor if self._hit_region(px, py) >= 0 else Qt.CrossCursor)

    def _apply_resize(self, px: float, py: float) -> None:
        drag = self._drag
        assert drag is not None
        r = self.regions[self.selected_idx]
        orig = drag["orig"]
        handle = drag["handle"]
        dx = px - drag["start_x"]
        dy = py - drag["start_y"]
        x, y, w, h = orig["x"], orig["y"], orig["w"], orig["h"]
        if "e" in handle:
            w = max(self.MIN_SIZE, w + dx)
        if "s" in handle:
            h = max(self.MIN_SIZE, h + dy)
        if "w" in handle:
            x = x + dx
            w = max(self.MIN_SIZE, w - dx)
        if "n" in handle:
            y = y + dy
            h = max(self.MIN_SIZE, h - dy)
        r["x"], r["y"], r["w"], r["h"] = x, y, w, h

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or not self._drag:
            return
        if self._drag["type"] == "create":
            px, py = self._to_image(event.position())
            w = abs(px - self._drag["start_x"])
            h = abs(py - self._drag["start_y"])
            if w > self.MIN_SIZE and h > self.MIN_SIZE:
                self._add_region(
                    min(px, self._drag["start_x"]),
                    min(py, self._drag["start_y"]),
                    w,
                    h,
                )
        else:
            self.changed.emit()
        self._drag = None
        self.update()

    def _add_region(self, x: float, y: float, w: float, h: float) -> None:
        if self.pending_label:
            field_id = self.pending_label
            if self.replace_same_label:
                self.regions = [r for r in self.regions if r["id"] != field_id]
        else:
            field_id = f"記述欄{len(self.regions) + 1}"
        self.regions.append(
            {
                "id": field_id,
                "displayName": field_id,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "order": len(self.regions),
                "ocrLang": "en",
            }
        )
        for i, r in enumerate(self.regions):
            r["order"] = i + 1
        self.selected_idx = len(self.regions) - 1
        self.changed.emit()


class AnswerRegionEditor(QScrollArea):
    """スクロール付きの領域エディタ本体（アプリからはこちらを使う）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        on_change: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._canvas = _EditorCanvas()
        self.setWidget(self._canvas)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        if on_change:
            self._canvas.changed.connect(on_change)
        if on_status:
            self._canvas.status.connect(on_status)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._canvas._update_scale()

    # --- Tkinter 版と同じ公開 API ---

    def has_image(self) -> bool:
        return self._canvas._image_bgr is not None

    def get_image_bgr(self) -> np.ndarray | None:
        return self._canvas._image_bgr

    def set_image(self, image_bgr: np.ndarray) -> None:
        self._canvas.set_image(image_bgr)

    def load_image_from_path(self, path: str) -> None:
        self.set_image(load_image_bgr(path))

    def set_regions(self, regions: list[dict[str, Any]]) -> None:
        rows = []
        for i, r in enumerate(regions or []):
            rows.append(
                {
                    "id": r.get("id") or f"記述欄{i + 1}",
                    "displayName": r.get("displayName") or r.get("id") or f"記述欄{i + 1}",
                    "x": float(r.get("x") or 0),
                    "y": float(r.get("y") or 0),
                    "w": float(r.get("width") or r.get("w") or 0),
                    "h": float(r.get("height") or r.get("h") or 0),
                    "order": int(r.get("order") or i + 1),
                    "ocrLang": "ja" if str(r.get("ocrLang") or "").lower() == "ja" else "en",
                }
            )
        self._canvas.regions = rows
        self._canvas.selected_idx = -1
        self._canvas.update()

    def get_regions(self) -> list[dict[str, Any]]:
        out = []
        for i, r in enumerate(self._canvas.regions):
            out.append(
                {
                    "id": r["id"],
                    "displayName": r.get("displayName") or r["id"],
                    "x": int(round(r["x"])),
                    "y": int(round(r["y"])),
                    "width": int(round(r["w"])),
                    "height": int(round(r["h"])),
                    "order": int(r.get("order") or i + 1),
                    "ocrLang": "ja" if str(r.get("ocrLang") or "").lower() == "ja" else "en",
                }
            )
        return out

    def delete_selected(self) -> None:
        c = self._canvas
        if c.selected_idx < 0:
            return
        c.regions.pop(c.selected_idx)
        c.selected_idx = -1
        for i, r in enumerate(c.regions):
            r["order"] = i + 1
        c.update()
        c.changed.emit()

    def select_region(self, index: int) -> None:
        if 0 <= index < len(self._canvas.regions):
            self._canvas.selected_idx = index
            self._canvas.update()

    def set_region_ocr_lang(self, index: int, lang: str) -> None:
        if 0 <= index < len(self._canvas.regions):
            self._canvas.regions[index]["ocrLang"] = "ja" if lang == "ja" else "en"

    def set_pending_label(self, label: str | None, *, replace_same: bool = True) -> None:
        """次にドラッグで作る矩形の ID を指定する（⑧欄種別 / ⑩slotKey 用）。"""
        self._canvas.pending_label = label
        self._canvas.replace_same_label = replace_same

    def clear_all_regions(self) -> None:
        self._canvas.regions = []
        self._canvas.selected_idx = -1
        self._canvas.update()
        self._canvas.changed.emit()
