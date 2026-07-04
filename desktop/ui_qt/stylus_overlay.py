"""スタイラス手書きオーバーレイ（パームリジェクション対応）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPointF, Qt, Signal, QEvent
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPen,
    QTabletEvent,
    QTouchEvent,
)
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui_qt.helpers import pil_to_qpixmap

DEFAULT_INK_COLOR = "#111827"
DEFAULT_BASE_WIDTH = 2.5


def is_stylus_tablet_event(event: QTabletEvent) -> bool:
    pt = event.pointerType()
    if pt in (
        QTabletEvent.PointerType.Pen,
        QTabletEvent.PointerType.Eraser,
    ):
        return True
    # Qt6: Unknown でも pressure があるペン入力を許容
    return float(event.pressure()) > 0.0 and pt != QTabletEvent.PointerType.Cursor


def is_finger_tablet_event(event: QTabletEvent) -> bool:
    return event.pointerType() in (
        QTabletEvent.PointerType.Unknown,
        QTabletEvent.PointerType.Finger,
    )


class InkOverlayWidget(QWidget):
    """記述欄クロップ上の最前面手書きレイヤー。"""

    strokes_changed = Signal()

    def __init__(
        self,
        *,
        field_id: str,
        native_w: int,
        native_h: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._field_id = field_id
        self._native_w = max(1, int(native_w))
        self._native_h = max(1, int(native_h))
        self._display_w = self._native_w
        self._display_h = self._native_h
        self._strokes: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._palm_rejection = True
        self._show_ink = True
        self._drawing_enabled = True
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self._apply_mouse_transparency()
        self.setFixedSize(self._display_w, self._display_h)

    def set_display_size(self, w: int, h: int) -> None:
        self._display_w = max(1, int(w))
        self._display_h = max(1, int(h))
        self.setFixedSize(self._display_w, self._display_h)
        self.update()

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)
        self._apply_mouse_transparency()

    def set_show_ink(self, visible: bool) -> None:
        self._show_ink = bool(visible)
        self.update()

    def set_drawing_enabled(self, enabled: bool) -> None:
        self._drawing_enabled = bool(enabled)

    def strokes(self) -> list[dict[str, Any]]:
        return list(self._strokes)

    def set_strokes(self, strokes: list[dict[str, Any]]) -> None:
        self._strokes = list(strokes or [])
        self.update()

    def clear_strokes(self) -> None:
        self._strokes = []
        self._current = None
        self.update()
        self.strokes_changed.emit()

    def _apply_mouse_transparency(self) -> None:
        # パームリジェクション ON: マウス/タッチは下のタイルへ透過、ペンだけ描画
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._palm_rejection)

    def _scale_x(self) -> float:
        return self._native_w / float(self._display_w)

    def _scale_y(self) -> float:
        return self._native_h / float(self._display_h)

    def _to_native(self, x: float, y: float) -> tuple[float, float]:
        sx, sy = self._scale_x(), self._scale_y()
        return (
            max(0.0, min(self._native_w, x * sx)),
            max(0.0, min(self._native_h, y * sy)),
        )

    def _to_display(self, x: float, y: float) -> tuple[float, float]:
        sx, sy = self._scale_x(), self._scale_y()
        if sx <= 0 or sy <= 0:
            return x, y
        return x / sx, y / sy

    def _start_stroke(self, x: float, y: float, pressure: float) -> None:
        nx, ny = self._to_native(x, y)
        self._current = {
            "fieldId": self._field_id,
            "color": DEFAULT_INK_COLOR,
            "alpha": 1.0,
            "baseWidth": DEFAULT_BASE_WIDTH,
            "points": [{"x": nx, "y": ny, "p": pressure}],
        }

    def _extend_stroke(self, x: float, y: float, pressure: float) -> None:
        if not self._current:
            return
        nx, ny = self._to_native(x, y)
        self._current["points"].append({"x": nx, "y": ny, "p": pressure})

    def _finish_stroke(self) -> None:
        if not self._current:
            return
        if len(self._current.get("points") or []) >= 1:
            self._strokes.append(self._current)
            self.strokes_changed.emit()
        self._current = None

    def _line_width(self, base: float, pressure: float) -> float:
        p = max(0.0, min(1.0, pressure))
        return max(1.0, base * (0.5 + 0.5 * p))

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._show_ink:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        all_strokes = list(self._strokes)
        if self._current:
            all_strokes.append(self._current)
        for stroke in all_strokes:
            self._paint_stroke(painter, stroke)

    def _paint_stroke(self, painter: QPainter, stroke: dict[str, Any]) -> None:
        points = stroke.get("points") or []
        if not points:
            return
        color = QColor(stroke.get("color") or DEFAULT_INK_COLOR)
        base_w = float(stroke.get("baseWidth") or DEFAULT_BASE_WIDTH)
        disp_scale = 1.0 / max(self._scale_x(), self._scale_y())
        if len(points) == 1:
            p = points[0]
            dx, dy = self._to_display(float(p["x"]), float(p["y"]))
            pr = float(p.get("p", 1.0))
            r = self._line_width(base_w * disp_scale, pr) / 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(dx, dy), r, r)
            return
        pen = QPen(color)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        for a, b in zip(points, points[1:], strict=False):
            ax, ay = self._to_display(float(a["x"]), float(a["y"]))
            bx, by = self._to_display(float(b["x"]), float(b["y"]))
            pr = float(b.get("p", 1.0))
            pen.setWidthF(self._line_width(base_w * disp_scale, pr))
            painter.setPen(pen)
            painter.drawLine(QPointF(ax, ay), QPointF(bx, by))

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        if not self._drawing_enabled:
            event.ignore()
            return
        if self._palm_rejection and is_finger_tablet_event(event):
            event.ignore()
            return
        if not is_stylus_tablet_event(event) and self._palm_rejection:
            event.ignore()
            return
        pos = event.position()
        pressure = float(event.pressure()) if event.pressure() >= 0 else 1.0
        t = event.type()
        if t == QEvent.Type.TabletPress:
            self._start_stroke(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletMove and self._current:
            self._extend_stroke(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletRelease:
            if self._current:
                self._finish_stroke()
                self.update()
            event.accept()
            return
        event.ignore()

    def touchEvent(self, event: QTouchEvent) -> None:  # noqa: N802
        if self._palm_rejection or not self._drawing_enabled:
            event.ignore()
            return
        points = event.points()
        if not points:
            event.ignore()
            return
        tp = points[0]
        pos = tp.position()
        state = tp.state()
        if state == Qt.TouchPointState.TouchPointPressed:
            self._start_stroke(pos.x(), pos.y(), 1.0)
            self.update()
            event.accept()
            return
        if state == Qt.TouchPointState.TouchPointMoved and self._current:
            self._extend_stroke(pos.x(), pos.y(), 1.0)
            self.update()
            event.accept()
            return
        if state in (Qt.TouchPointState.TouchPointReleased, Qt.TouchPointState.TouchPointStationary):
            if self._current:
                self._finish_stroke()
                self.update()
            event.accept()
            return
        event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._palm_rejection or not self._drawing_enabled:
            event.ignore()
            return
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        self._start_stroke(event.position().x(), event.position().y(), 1.0)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._palm_rejection or not self._drawing_enabled or not self._current:
            event.ignore()
            return
        if not (event.buttons() & Qt.LeftButton):
            event.ignore()
            return
        self._extend_stroke(event.position().x(), event.position().y(), 1.0)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._palm_rejection or not self._drawing_enabled:
            event.ignore()
            return
        if self._current:
            self._finish_stroke()
            self.update()
        event.accept()


class CropInkImageStack(QWidget):
    """クロップ画像 + 手書き最前面レイヤー。"""

    image_clicked = Signal()

    def __init__(
        self,
        *,
        pil_image,
        field_id: str,
        strokes: list[dict[str, Any]] | None = None,
        zoom: float = 1.0,
        on_strokes_changed: Callable[[list[dict[str, Any]]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_strokes_changed = on_strokes_changed
        self._palm_rejection = True
        self._show_ink = True
        self._click_forward = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        native_w = max(1, int(pil_image.width))
        native_h = max(1, int(pil_image.height))
        disp_w = max(40, int(native_w * zoom))
        disp_h = max(1, int(native_h * (disp_w / native_w)))

        self.image_label = QLabel()
        pix = pil_to_qpixmap(pil_image).scaled(
            disp_w,
            disp_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(pix)
        self.image_label.setFixedSize(pix.size())
        self.image_label.setStyleSheet("border: none; background: transparent;")
        self._click_forward = True

        self.ink_overlay = InkOverlayWidget(
            field_id=field_id,
            native_w=native_w,
            native_h=native_h,
        )
        self.ink_overlay.set_display_size(pix.width(), pix.height())
        if strokes:
            self.ink_overlay.set_strokes(strokes)
        self.ink_overlay.strokes_changed.connect(self._emit_strokes_changed)
        self.ink_overlay.raise_()

        container = QWidget()
        container.setFixedSize(pix.size())
        self.image_label.setParent(container)
        self.image_label.move(0, 0)
        self.ink_overlay.setParent(container)
        self.ink_overlay.move(0, 0)
        lay.addWidget(container)
        self.setFixedSize(container.size())
        self._wire_image_click()

    def _emit_strokes_changed(self) -> None:
        if self._on_strokes_changed:
            self._on_strokes_changed(self.ink_overlay.strokes())

    def _wire_image_click(self) -> None:
        def click_handler(event: QMouseEvent) -> None:
            if self._click_forward and self._palm_rejection and event.button() == Qt.LeftButton:
                self.image_clicked.emit()
                event.accept()
                return
            QLabel.mousePressEvent(self.image_label, event)

        self.image_label.mousePressEvent = click_handler  # type: ignore[method-assign]

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)
        self._click_forward = bool(enabled)
        self.ink_overlay.set_palm_rejection(enabled)

    def set_show_ink(self, visible: bool) -> None:
        self._show_ink = bool(visible)
        self.ink_overlay.set_show_ink(visible)

    def set_drawing_enabled(self, enabled: bool) -> None:
        self.ink_overlay.set_drawing_enabled(enabled)
