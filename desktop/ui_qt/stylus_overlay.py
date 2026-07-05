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
    QPointingDevice,
    QTabletEvent,
    QTouchEvent,
)
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui_qt.helpers import pil_to_qpixmap

DEFAULT_INK_COLOR = "#111827"
DEFAULT_BASE_WIDTH = 2.5

# PySide6 では pointerType は QTabletEvent ではなく QPointingDevice 側の列挙
_Pen = QPointingDevice.PointerType.Pen
_Eraser = QPointingDevice.PointerType.Eraser
_Finger = QPointingDevice.PointerType.Finger


def _event_pressure(event: QTabletEvent | QMouseEvent) -> float:
    try:
        pr = float(event.pressure())
        if pr >= 0:
            return max(0.0, min(1.0, pr))
    except (AttributeError, TypeError, ValueError):
        pass
    return 1.0


def is_stylus_tablet_event(event: QTabletEvent) -> bool:
    pt = event.pointerType()
    if pt in (_Pen, _Eraser):
        return True
    if pt == _Finger:
        return False
    # Windows Ink: Unknown/Generic でも筆圧があればペン扱い
    if _event_pressure(event) > 0.01:
        return True
    # 初回 TabletPress では筆圧 0 の端末があるため Unknown はペンとみなす
    return pt not in (_Finger,)


def is_finger_tablet_event(event: QTabletEvent) -> bool:
    return event.pointerType() == _Finger


def _mouse_synthesized_by_system(event: QMouseEvent) -> bool:
    """Qt バージョン差を吸収してタブレット由来の合成マウスか判定。"""
    synth = getattr(Qt, "MouseEventFlag", None)
    if synth is None:
        return False
    flag = getattr(synth, "MouseEventSynthesizedBySystem", None)
    if flag is None:
        return False
    try:
        return bool(event.flags() & flag)
    except (AttributeError, TypeError):
        return False


def is_pen_mouse_event(event: QMouseEvent) -> bool:
    pt = event.pointerType()
    if pt in (_Pen, _Eraser):
        return True
    if pt == _Finger:
        return False
    dev = event.pointingDevice()
    if dev is not None:
        dpt = dev.pointerType()
        if dpt in (_Pen, _Eraser):
            return True
        if dpt == _Finger:
            return False
    # 合成マウス: 筆圧 0< p <1 ならペン
    pr = _event_pressure(event)
    if 0.0 < pr < 1.0:
        return True
    # タブレット由来の合成マウス（Windows Ink 等・対応 Qt のみ）
    if _mouse_synthesized_by_system(event):
        return pt != _Finger
    return False


class InkOverlayWidget(QWidget):
    """記述欄クロップ上の最前面手書きレイヤー。"""

    strokes_changed = Signal()
    click_through = Signal()

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
        self._pen_active = False
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WA_TabletTracking, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFixedSize(self._display_w, self._display_h)

    def set_display_size(self, w: int, h: int) -> None:
        self._display_w = max(1, int(w))
        self._display_h = max(1, int(h))
        self.setFixedSize(self._display_w, self._display_h)
        self.update()

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)

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
        self._pen_active = False
        self.update()
        self.strokes_changed.emit()

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
        self._pen_active = False

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

    def _should_draw_tablet(self, event: QTabletEvent) -> bool:
        if not self._drawing_enabled:
            return False
        if self._palm_rejection:
            if is_finger_tablet_event(event):
                return False
            return is_stylus_tablet_event(event)
        return True

    def _should_draw_mouse(self, event: QMouseEvent) -> bool:
        if not self._drawing_enabled:
            return False
        if self._palm_rejection:
            return is_pen_mouse_event(event) or self._pen_active
        return True

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        pos = event.position()
        pressure = _event_pressure(event)
        t = event.type()

        if not self._should_draw_tablet(event):
            if (
                self._palm_rejection
                and t == QEvent.Type.TabletPress
                and is_finger_tablet_event(event)
            ):
                self.click_through.emit()
            event.accept()
            return

        if t == QEvent.Type.TabletPress:
            self._pen_active = True
            self._start_stroke(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletMove:
            if not self._current:
                if self._pen_active or pressure > 0.01:
                    self._pen_active = True
                    self._start_stroke(pos.x(), pos.y(), pressure)
            else:
                self._extend_stroke(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletRelease:
            if self._current:
                self._finish_stroke()
                self.update()
            else:
                self._pen_active = False
            event.accept()
            return
        event.ignore()

    def touchEvent(self, event: QTouchEvent) -> None:  # noqa: N802
        if self._palm_rejection or not self._drawing_enabled:
            if self._palm_rejection:
                points = event.points()
                if points and points[0].state() == Qt.TouchPointState.TouchPointPressed:
                    self.click_through.emit()
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
        if state == Qt.TouchPointState.TouchPointReleased:
            if self._current:
                self._finish_stroke()
                self.update()
            event.accept()
            return
        event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        if not self._should_draw_mouse(event):
            self.click_through.emit()
            event.accept()
            return
        self._pen_active = is_pen_mouse_event(event) or not self._palm_rejection
        self._start_stroke(event.position().x(), event.position().y(), _event_pressure(event))
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._should_draw_mouse(event):
            event.ignore()
            return
        if not (event.buttons() & Qt.LeftButton):
            event.ignore()
            return
        if not self._current:
            self._start_stroke(
                event.position().x(), event.position().y(), _event_pressure(event)
            )
        else:
            self._extend_stroke(
                event.position().x(), event.position().y(), _event_pressure(event)
            )
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._should_draw_mouse(event) and not self._current:
            event.ignore()
            return
        if self._current:
            self._finish_stroke()
            self.update()
        else:
            self._pen_active = False
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

        self.ink_overlay = InkOverlayWidget(
            field_id=field_id,
            native_w=native_w,
            native_h=native_h,
        )
        self.ink_overlay.set_display_size(pix.width(), pix.height())
        if strokes:
            self.ink_overlay.set_strokes(strokes)
        self.ink_overlay.strokes_changed.connect(self._emit_strokes_changed)
        self.ink_overlay.click_through.connect(self.image_clicked.emit)

        container = QWidget()
        container.setFixedSize(pix.size())
        container.setAttribute(Qt.WA_TabletTracking, True)
        self.image_label.setParent(container)
        self.image_label.move(0, 0)
        self.ink_overlay.setParent(container)
        self.ink_overlay.move(0, 0)
        self.ink_overlay.raise_()
        lay.addWidget(container)
        self.setFixedSize(container.size())

    def _emit_strokes_changed(self) -> None:
        if self._on_strokes_changed:
            self._on_strokes_changed(self.ink_overlay.strokes())

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)
        self.ink_overlay.set_palm_rejection(enabled)

    def set_show_ink(self, visible: bool) -> None:
        self._show_ink = bool(visible)
        self.ink_overlay.set_show_ink(visible)

    def set_drawing_enabled(self, enabled: bool) -> None:
        self.ink_overlay.set_drawing_enabled(enabled)
