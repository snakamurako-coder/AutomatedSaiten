"""スタイラス手書きオーバーレイ（パームリジェクション対応）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, QPointF, Qt, Signal, QEvent
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
DEFAULT_ERASER_RADIUS = 18.0

ERASER_MODE_PIXEL = "pixel"
ERASER_MODE_STROKE = "stroke"

TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_NONE = "none"

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


def is_eraser_tablet_event(event: QTabletEvent) -> bool:
    return event.pointerType() == _Eraser


def is_eraser_mouse_event(event: QMouseEvent) -> bool:
    if event.pointerType() == _Eraser:
        return True
    dev = event.pointingDevice()
    return dev is not None and dev.pointerType() == _Eraser


def _dist_point_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq <= 1e-9:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


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
    if is_eraser_mouse_event(event):
        return False
    pt = event.pointerType()
    if pt == _Pen:
        return True
    if pt == _Eraser:
        return False
    if pt == _Finger:
        return False
    dev = event.pointingDevice()
    if dev is not None:
        dpt = dev.pointerType()
        if dpt == _Pen:
            return True
        if dpt == _Eraser:
            return False
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
        self._eraser_active = False
        self._eraser_mode = ERASER_MODE_PIXEL
        self._tool_mode = TOOL_NONE
        self._brush_color = DEFAULT_INK_COLOR
        self._brush_width = DEFAULT_BASE_WIDTH
        self._brush_alpha = 1.0
        self._software_eraser = False
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

    def set_eraser_mode(self, mode: str) -> None:
        m = str(mode or ERASER_MODE_PIXEL).strip().lower()
        self._eraser_mode = m if m in (ERASER_MODE_PIXEL, ERASER_MODE_STROKE) else ERASER_MODE_PIXEL

    def set_tool_mode(self, mode: str) -> None:
        m = str(mode or TOOL_NONE).strip().lower()
        if m not in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_NONE):
            m = TOOL_NONE
        self._tool_mode = m
        self._software_eraser = m == TOOL_ERASER
        self.setAttribute(Qt.WA_TransparentForMouseEvents, m == TOOL_TEXT)

    def _stylus_may_draw(self) -> bool:
        if self._tool_mode == TOOL_TEXT and not self._palm_rejection:
            return False
        if self._tool_mode == TOOL_ERASER:
            return False
        if self._palm_rejection:
            return True
        return self._tool_mode in (TOOL_PEN, TOOL_NONE)

    def _pointer_may_draw(self) -> bool:
        if self._tool_mode == TOOL_TEXT:
            return False
        if self._palm_rejection:
            return False
        return self._tool_mode in (TOOL_PEN, TOOL_ERASER)

    def _emit_click_through(self) -> None:
        if self._tool_mode != TOOL_TEXT:
            self.click_through.emit()

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self._brush_color = str(color or DEFAULT_INK_COLOR)
        self._brush_width = max(0.5, float(width))
        self._brush_alpha = max(0.0, min(1.0, float(alpha)))

    def strokes(self) -> list[dict[str, Any]]:
        return list(self._strokes)

    def set_strokes(self, strokes: list[dict[str, Any]]) -> None:
        self._strokes = list(strokes or [])
        self.update()

    def clear_strokes(self) -> None:
        self._strokes = []
        self._current = None
        self._pen_active = False
        self._eraser_active = False
        self.update()
        self.strokes_changed.emit()

    def _cancel_current_stroke(self) -> None:
        self._current = None
        self._pen_active = False

    def _eraser_radius_native(self, pressure: float) -> float:
        p = max(0.0, min(1.0, pressure))
        return DEFAULT_ERASER_RADIUS * (0.75 + 0.25 * p)

    def _stroke_hit_radius(self, stroke: dict[str, Any], eraser_r: float) -> float:
        base_w = float(stroke.get("baseWidth") or DEFAULT_BASE_WIDTH)
        return eraser_r + base_w * 0.5

    def _stroke_touched_by_eraser(
        self,
        stroke: dict[str, Any],
        cx: float,
        cy: float,
        eraser_r: float,
    ) -> bool:
        points = stroke.get("points") or []
        if not points:
            return False
        hit_r = self._stroke_hit_radius(stroke, eraser_r)
        hit_r_sq = hit_r * hit_r
        for i, p in enumerate(points):
            px, py = float(p["x"]), float(p["y"])
            if (px - cx) ** 2 + (py - cy) ** 2 <= hit_r_sq:
                return True
            if i > 0:
                ap = points[i - 1]
                d = _dist_point_to_segment(
                    cx,
                    cy,
                    float(ap["x"]),
                    float(ap["y"]),
                    px,
                    py,
                )
                if d <= hit_r:
                    return True
        return False

    def _erase_stroke_pixel(
        self,
        stroke: dict[str, Any],
        cx: float,
        cy: float,
        eraser_r: float,
    ) -> list[dict[str, Any]]:
        points = stroke.get("points") or []
        if not points:
            return []
        hit_r = self._stroke_hit_radius(stroke, eraser_r)
        fragments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for i, p in enumerate(points):
            px, py = float(p["x"]), float(p["y"])
            hit = (px - cx) ** 2 + (py - cy) ** 2 <= hit_r * hit_r
            if i > 0:
                ap = points[i - 1]
                d = _dist_point_to_segment(
                    cx,
                    cy,
                    float(ap["x"]),
                    float(ap["y"]),
                    px,
                    py,
                )
                if d <= hit_r:
                    hit = True
            if hit:
                if current:
                    fragments.append(current)
                    current = []
            else:
                current.append(p)
        if current:
            fragments.append(current)
        return [{**stroke, "points": frag} for frag in fragments if frag]

    def _erase_at(self, x: float, y: float, pressure: float) -> bool:
        nx, ny = self._to_native(x, y)
        eraser_r = self._eraser_radius_native(pressure)
        if self._eraser_mode == ERASER_MODE_STROKE:
            return self._erase_at_stroke(nx, ny, eraser_r)
        return self._erase_at_pixel(nx, ny, eraser_r)

    def _erase_at_stroke(self, nx: float, ny: float, eraser_r: float) -> bool:
        before_n = len(self._strokes)
        new_strokes = [
            s
            for s in self._strokes
            if not self._stroke_touched_by_eraser(s, nx, ny, eraser_r)
        ]
        if len(new_strokes) == before_n:
            return False
        self._strokes = new_strokes
        self.strokes_changed.emit()
        return True

    def _erase_at_pixel(self, nx: float, ny: float, eraser_r: float) -> bool:
        before_pts = sum(len(s.get("points") or []) for s in self._strokes)
        before_n = len(self._strokes)
        new_strokes: list[dict[str, Any]] = []
        for stroke in self._strokes:
            new_strokes.extend(self._erase_stroke_pixel(stroke, nx, ny, eraser_r))
        after_pts = sum(len(s.get("points") or []) for s in new_strokes)
        if before_pts == after_pts and before_n == len(new_strokes):
            return False
        self._strokes = new_strokes
        self.strokes_changed.emit()
        return True

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
            "color": self._brush_color,
            "alpha": self._brush_alpha,
            "baseWidth": self._brush_width,
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
        alpha = float(stroke.get("alpha", 1.0))
        color.setAlphaF(max(0.0, min(1.0, alpha)))
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

    def _should_handle_tablet(self, event: QTabletEvent) -> bool:
        if not self._stylus_may_draw():
            return False
        if is_eraser_tablet_event(event):
            return True
        if self._palm_rejection:
            if is_finger_tablet_event(event):
                return False
            return is_stylus_tablet_event(event)
        if self._tool_mode == TOOL_NONE:
            return is_stylus_tablet_event(event)
        if self._tool_mode not in (TOOL_PEN, TOOL_ERASER):
            return False
        return True

    def _should_draw_tablet(self, event: QTabletEvent) -> bool:
        if is_eraser_tablet_event(event) or self._software_eraser:
            return False
        return self._should_handle_tablet(event)

    def _should_erase_tablet(self, event: QTabletEvent) -> bool:
        if self._tool_mode == TOOL_TEXT and not self._palm_rejection:
            return False
        if not self._stylus_may_draw() and not self._software_eraser:
            return False
        return is_eraser_tablet_event(event) or self._eraser_active or self._software_eraser

    def _should_draw_mouse(self, event: QMouseEvent) -> bool:
        if not self._pointer_may_draw():
            return False
        if is_eraser_mouse_event(event) or self._software_eraser:
            return False
        return True

    def _should_erase_mouse(self, event: QMouseEvent) -> bool:
        if self._tool_mode == TOOL_TEXT:
            return False
        if not self._pointer_may_draw() and not self._software_eraser:
            return False
        return is_eraser_mouse_event(event) or self._eraser_active or self._software_eraser

    def _handle_tablet_eraser(self, event: QTabletEvent) -> None:
        pos = event.position()
        pressure = _event_pressure(event)
        t = event.type()
        if t == QEvent.Type.TabletPress:
            self._eraser_active = True
            self._cancel_current_stroke()
            self._erase_at(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletMove:
            if self._eraser_active or is_eraser_tablet_event(event):
                self._eraser_active = True
                self._cancel_current_stroke()
                self._erase_at(pos.x(), pos.y(), pressure)
                self.update()
            event.accept()
            return
        if t == QEvent.Type.TabletRelease:
            self._eraser_active = False
            self.update()
            event.accept()
            return
        event.ignore()

    def _handle_mouse_eraser(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton and not (event.buttons() & Qt.LeftButton):
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._eraser_active = False
                event.accept()
            else:
                event.ignore()
            return
        pressure = _event_pressure(event)
        pos = event.position()
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            self._eraser_active = True
            self._cancel_current_stroke()
            self._erase_at(pos.x(), pos.y(), pressure)
            self.update()
            event.accept()
            return
        if t == QEvent.Type.MouseMove:
            if self._eraser_active:
                self._erase_at(pos.x(), pos.y(), pressure)
                self.update()
            event.accept()
            return
        if t == QEvent.Type.MouseButtonRelease:
            self._eraser_active = False
            event.accept()
            return
        event.ignore()

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        if self._tool_mode == TOOL_TEXT and not self._palm_rejection:
            event.ignore()
            return
        if self._should_erase_tablet(event):
            self._handle_tablet_eraser(event)
            return

        pos = event.position()
        pressure = _event_pressure(event)
        t = event.type()

        if not self._should_draw_tablet(event):
            if (
                self._palm_rejection
                and t == QEvent.Type.TabletPress
                and is_finger_tablet_event(event)
            ):
                self._emit_click_through()
            event.accept()
            return

        self._eraser_active = False
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
        if self._tool_mode == TOOL_TEXT and not self._palm_rejection:
            event.ignore()
            return
        points = event.points()
        pressed = (
            points
            and points[0].state() == Qt.TouchPointState.TouchPointPressed
        )
        if self._palm_rejection or not self._pointer_may_draw():
            if pressed:
                self._emit_click_through()
            event.ignore()
            return
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
        if self._should_erase_mouse(event):
            self._handle_mouse_eraser(event)
            return
        if not self._should_draw_mouse(event):
            self._emit_click_through()
            event.ignore()
            return
        self._eraser_active = False
        self._pen_active = True
        self._start_stroke(event.position().x(), event.position().y(), _event_pressure(event))
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._should_erase_mouse(event):
            self._handle_mouse_eraser(event)
            return
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
        if self._should_erase_mouse(event) or self._eraser_active:
            self._handle_mouse_eraser(event)
            return
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
    """クロップ画像 + 手書き + テキスト注釈レイヤー。"""

    image_clicked = Signal()

    def __init__(
        self,
        *,
        pil_image,
        field_id: str,
        result_id: int = 0,
        strokes: list[dict[str, Any]] | None = None,
        annotations: list[dict[str, Any]] | None = None,
        zoom: float = 1.0,
        on_strokes_changed: Callable[[list[dict[str, Any]]], None] | None = None,
        on_annotations_changed: Callable[[list[dict[str, Any]]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._result_id = int(result_id or 0)
        self._field_id = field_id
        self._on_strokes_changed = on_strokes_changed
        self._on_annotations_changed = on_annotations_changed
        self._palm_rejection = True
        self._show_ink = True
        self._show_text = True
        self._tool_mode = TOOL_NONE
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
        self.ink_overlay.click_through.connect(self._on_ink_click_through)

        from ui_qt.floating_palette.text_box_layer import TextBoxLayer

        self.text_layer = TextBoxLayer(
            native_w=native_w,
            native_h=native_h,
            annotations=annotations,
            on_changed=self._emit_annotations_changed,
        )
        self.text_layer.set_display_size(pix.width(), pix.height())

        self.container = QWidget()
        self.container.setFixedSize(pix.size())
        self.container.setAttribute(Qt.WA_TabletTracking, True)
        self.image_label.setParent(self.container)
        self.image_label.move(0, 0)
        self.ink_overlay.setParent(self.container)
        self.ink_overlay.move(0, 0)
        self.text_layer.setParent(self.container)
        self.text_layer.move(0, 0)
        self.container.installEventFilter(self)
        self.ink_overlay.installEventFilter(self)
        self.text_layer.installEventFilter(self)
        self._sync_layer_order()
        lay.addWidget(self.container)
        self.setFixedSize(self.container.size())

    @property
    def result_id(self) -> int:
        return self._result_id

    def _on_ink_click_through(self) -> None:
        if self._tool_mode != TOOL_TEXT:
            self.image_clicked.emit()

    def _try_place_text_at(self, source: QWidget, pos) -> bool:
        if self._tool_mode != TOOL_TEXT:
            return False
        gp = source.mapToGlobal(QPoint(int(pos.x()), int(pos.y())))
        local = self.text_layer.mapFromGlobal(gp)
        lx, ly = int(local.x()), int(local.y())
        if self.text_layer.point_on_any_box(lx, ly):
            return False
        self.text_layer.place_box_at(float(lx), float(ly))
        return True

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if self._tool_mode != TOOL_TEXT:
            return super().eventFilter(watched, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.LeftButton:
                return super().eventFilter(watched, event)
            if watched in (self.container, self.ink_overlay, self.text_layer):
                if self._try_place_text_at(watched, event.position()):
                    return True
        elif et == QEvent.Type.TabletPress:
            if watched in (self.container, self.ink_overlay, self.text_layer):
                if self._try_place_text_at(watched, event.position()):
                    return True
        return super().eventFilter(watched, event)

    def _sync_layer_order(self) -> None:
        # 最背面: 画像 → 中間: テキスト → 最前面: 手書き
        self.image_label.lower()
        self.text_layer.raise_()
        self.ink_overlay.raise_()

    def _emit_strokes_changed(self) -> None:
        if self._on_strokes_changed:
            self._on_strokes_changed(self.ink_overlay.strokes())

    def _emit_annotations_changed(self, items: list[dict[str, Any]]) -> None:
        if self._on_annotations_changed:
            self._on_annotations_changed(items)

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)
        self.ink_overlay.set_palm_rejection(enabled)
        self.text_layer.set_palm_rejection(enabled)

    def set_show_ink(self, visible: bool) -> None:
        self._show_ink = bool(visible)
        self.ink_overlay.set_show_ink(visible)

    def set_show_text(self, visible: bool) -> None:
        self._show_text = bool(visible)
        self.text_layer.set_show_text(visible)

    def set_drawing_enabled(self, enabled: bool) -> None:
        self.ink_overlay.set_drawing_enabled(enabled)

    def set_eraser_mode(self, mode: str) -> None:
        self.ink_overlay.set_eraser_mode(mode)

    def set_tool_mode(self, mode: str) -> None:
        self._tool_mode = mode
        is_text = mode == TOOL_TEXT
        self.ink_overlay.set_tool_mode(mode)
        self.ink_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, is_text)
        self.text_layer.setAttribute(Qt.WA_TransparentForMouseEvents, not is_text)
        self.text_layer.set_placement_mode(is_text)
        self._sync_layer_order()

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self.ink_overlay.set_brush(color, width, alpha)
