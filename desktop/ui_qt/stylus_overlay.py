"""スタイラス手書きオーバーレイ（パームリジェクション対応）。"""

from __future__ import annotations

import copy
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
TOOL_PHRASE = "phrase"
TOOL_NONE = "none"


def _is_text_like_tool(mode: str) -> bool:
    return mode in (TOOL_TEXT, TOOL_PHRASE)

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
    ink_history_commit = Signal(object, object)  # before strokes, after strokes
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
        self._eraser_session_before: list[dict[str, Any]] | None = None
        self._eraser_session_dirty = False
        self._before_draw_cb: Callable[[], None] | None = None
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
        if m not in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_PHRASE, TOOL_NONE):
            m = TOOL_NONE
        self._tool_mode = m
        self._software_eraser = m == TOOL_ERASER

    def _stylus_may_draw(self) -> bool:
        if _is_text_like_tool(self._tool_mode):
            return False
        if self._tool_mode == TOOL_ERASER:
            return False
        if self._palm_rejection:
            return True
        return self._tool_mode in (TOOL_PEN, TOOL_NONE)

    def _pointer_may_draw(self) -> bool:
        if _is_text_like_tool(self._tool_mode):
            return False
        if self._palm_rejection:
            return False
        return self._tool_mode in (TOOL_PEN, TOOL_ERASER)

    def _emit_click_through(self) -> None:
        if not _is_text_like_tool(self._tool_mode):
            self.click_through.emit()

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self._brush_color = str(color or DEFAULT_INK_COLOR)
        self._brush_width = max(0.5, float(width))
        self._brush_alpha = max(0.0, min(1.0, float(alpha)))

    def set_before_draw_callback(self, cb: Callable[[], None] | None) -> None:
        self._before_draw_cb = cb

    def _notify_before_draw(self) -> None:
        if self._before_draw_cb:
            self._before_draw_cb()

    def strokes(self) -> list[dict[str, Any]]:
        return list(self._strokes)

    def set_strokes(self, strokes: list[dict[str, Any]]) -> None:
        self._strokes = list(strokes or [])
        self.update()

    def clear_strokes(self) -> None:
        if not self._strokes and not self._current:
            return
        before = copy.deepcopy(self._strokes)
        self._strokes = []
        self._current = None
        self._pen_active = False
        self._eraser_active = False
        self._finish_eraser_session()
        self.update()
        self.ink_history_commit.emit(before, [])
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
        self._eraser_session_dirty = True
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
        self._eraser_session_dirty = True
        self.strokes_changed.emit()
        return True

    def _begin_eraser_session(self) -> None:
        if self._eraser_session_before is None:
            self._eraser_session_before = copy.deepcopy(self._strokes)
            self._eraser_session_dirty = False

    def _finish_eraser_session(self) -> None:
        if self._eraser_session_before is None:
            return
        before = self._eraser_session_before
        after = copy.deepcopy(self._strokes)
        dirty = self._eraser_session_dirty
        self._eraser_session_before = None
        self._eraser_session_dirty = False
        if dirty and before != after:
            self.ink_history_commit.emit(before, after)

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
        if self._current is None:
            self._notify_before_draw()
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
            before = copy.deepcopy(self._strokes)
            self._strokes.append(self._current)
            self.ink_history_commit.emit(before, copy.deepcopy(self._strokes))
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
        if _is_text_like_tool(self._tool_mode):
            return False
        if not self._stylus_may_draw() and not self._software_eraser:
            return False
        return is_eraser_tablet_event(event) or self._eraser_active or self._software_eraser

    def _should_draw_mouse(self, event: QMouseEvent) -> bool:
        if is_eraser_mouse_event(event) or self._software_eraser:
            return False
        if _is_text_like_tool(self._tool_mode):
            return False
        if not self._pointer_may_draw():
            return False
        return True

    def _should_erase_mouse(self, event: QMouseEvent) -> bool:
        if _is_text_like_tool(self._tool_mode):
            return False
        if not self._pointer_may_draw() and not self._software_eraser:
            return False
        return is_eraser_mouse_event(event) or self._eraser_active or self._software_eraser

    def _handle_tablet_eraser(self, event: QTabletEvent) -> None:
        pos = event.position()
        pressure = _event_pressure(event)
        t = event.type()
        if t == QEvent.Type.TabletPress:
            self._notify_before_draw()
            self._eraser_active = True
            self._begin_eraser_session()
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
            self._finish_eraser_session()
            self.update()
            event.accept()
            return
        event.ignore()

    def _handle_mouse_eraser(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton and not (event.buttons() & Qt.LeftButton):
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._eraser_active = False
                self._finish_eraser_session()
                event.accept()
            else:
                event.ignore()
            return
        pressure = _event_pressure(event)
        pos = event.position()
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            self._notify_before_draw()
            self._eraser_active = True
            self._begin_eraser_session()
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
            self._finish_eraser_session()
            event.accept()
            return
        event.ignore()

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        if _is_text_like_tool(self._tool_mode):
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
        if _is_text_like_tool(self._tool_mode):
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
        if self._current and is_pen_mouse_event(event) and _mouse_synthesized_by_system(event):
            event.accept()
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
        self._before_ink_draw: Callable[[], None] | None = None
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
        self.ink_overlay.set_before_draw_callback(self._before_ink_stroke)

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
        self.container.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.image_label.setParent(self.container)
        self.image_label.move(0, 0)
        self.ink_overlay.setParent(self.container)
        self.ink_overlay.move(0, 0)
        self.text_layer.setParent(self.container)
        self.text_layer.move(0, 0)
        self.container.installEventFilter(self)
        self.image_label.installEventFilter(self)
        self.ink_overlay.installEventFilter(self)
        self.text_layer.installEventFilter(self)
        self._sync_layer_order()
        lay.addWidget(self.container)
        self.setFixedSize(self.container.size())
        self.sync_place_cursor()

    def sync_place_cursor(self) -> None:
        cross = self.text_layer.has_speech_place_pending() or (
            self._tool_mode in (TOOL_TEXT, TOOL_PHRASE)
            and getattr(self.text_layer, "_placement_mode", False)
        )
        cursor = Qt.CursorShape.CrossCursor if cross else Qt.CursorShape.ArrowCursor
        if cross:
            self.container.setCursor(cursor)
            self.text_layer.setCursor(cursor)
        else:
            self.container.unsetCursor()
            if not getattr(self.text_layer, "_placement_mode", False):
                self.text_layer.unsetCursor()

    @property
    def result_id(self) -> int:
        return self._result_id

    @property
    def field_id(self) -> str:
        return self._field_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "strokes": copy.deepcopy(self.ink_overlay.strokes()),
            "annotations": self.text_layer.annotations(),
        }

    def apply_snapshot(self, snap: dict[str, Any]) -> None:
        strokes = copy.deepcopy(snap.get("strokes") or [])
        annotations = copy.deepcopy(snap.get("annotations") or [])
        self.ink_overlay.set_strokes(strokes)
        self.text_layer.set_annotations(annotations)
        self._emit_strokes_changed()
        self.text_layer.persist_annotations()

    def _before_ink_stroke(self) -> None:
        if self._before_ink_draw:
            self._before_ink_draw()
        else:
            self.text_layer.finish_all_editing()

    def set_before_ink_draw(self, cb: Callable[[], None] | None) -> None:
        self._before_ink_draw = cb

    def _on_ink_click_through(self) -> None:
        if not _is_text_like_tool(self._tool_mode):
            self.image_clicked.emit()

    def _map_to_text_layer(self, source: QWidget, pos) -> QPointF:
        from PySide6.QtCore import QPoint

        gp = source.mapToGlobal(QPoint(int(pos.x()), int(pos.y())))
        return self.text_layer.mapFromGlobal(gp)

    def _placement_pending(self) -> bool:
        return self.text_layer.has_speech_place_pending()

    def _is_text_placement_event(self, event) -> bool:
        if self._placement_pending():
            if isinstance(event, QTabletEvent):
                return event.type() in (
                    QEvent.Type.TabletPress,
                    QEvent.Type.TabletMove,
                    QEvent.Type.TabletRelease,
                )
            if isinstance(event, QTouchEvent):
                return event.type() == QEvent.Type.TouchBegin
            if isinstance(event, QMouseEvent):
                et = event.type()
                if et == QEvent.Type.MouseMove:
                    return bool(event.buttons() & Qt.LeftButton)
                if et == QEvent.Type.MouseButtonRelease:
                    return event.button() == Qt.LeftButton
                if event.button() != Qt.LeftButton and et == QEvent.Type.MouseButtonPress:
                    return False
                return True
            return False
        if isinstance(event, QTabletEvent):
            if self._palm_rejection and is_stylus_tablet_event(event):
                return False
            return True
        if isinstance(event, QMouseEvent):
            et = event.type()
            if et == QEvent.Type.MouseMove:
                return bool(event.buttons() & Qt.LeftButton)
            if et == QEvent.Type.MouseButtonRelease:
                return event.button() == Qt.LeftButton
            if event.button() != Qt.LeftButton:
                return False
            if self._palm_rejection and is_pen_mouse_event(event):
                return False
            return True
        return False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if not _is_text_like_tool(self._tool_mode) and not self._placement_pending():
            return super().eventFilter(watched, event)
        watched_layers = (
            self.container,
            self.image_label,
            self.ink_overlay,
            self.text_layer,
        )
        if watched not in watched_layers:
            return super().eventFilter(watched, event)
        et = event.type()
        placement_types = (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.TabletPress,
            QEvent.Type.TabletMove,
            QEvent.Type.TabletRelease,
            QEvent.Type.TouchBegin,
        )
        if et not in placement_types:
            return super().eventFilter(watched, event)
        if not self._is_text_placement_event(event):
            return super().eventFilter(watched, event)
        if (
            watched is self.text_layer
            and isinstance(event, QMouseEvent)
            and not self._placement_pending()
        ):
            if self.text_layer.is_placing():
                return super().eventFilter(watched, event)
            et = event.type()
            if et in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
            ):
                pos = event.position()
                lx, ly = int(pos.x()), int(pos.y())
                if self.text_layer.childAt(lx, ly) is not None:
                    return super().eventFilter(watched, event)
        elif watched is self.text_layer and isinstance(event, QMouseEvent):
            if self.text_layer.is_placing():
                return super().eventFilter(watched, event)
        if et == QEvent.Type.TouchBegin and isinstance(event, QTouchEvent):
            points = event.points()
            if not points or points[0].state() != Qt.TouchPointState.TouchPointPressed:
                return super().eventFilter(watched, event)
            gp = points[0].position()
            if watched is self.text_layer:
                local = QPointF(gp)
            else:
                gpos = watched.mapToGlobal(QPoint(int(gp.x()), int(gp.y())))
                local = self.text_layer.mapFromGlobal(gpos)
        elif watched is self.text_layer:
            local = event.position()
        else:
            local = self._map_to_text_layer(watched, event.position())
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.TabletPress, QEvent.Type.TouchBegin):
            if self.text_layer.handle_click_place_event(et, local, event):
                return True
        if self.text_layer.handle_placement_event(et, local, event):
            return True
        return super().eventFilter(watched, event)

    def _sync_layer_order(self) -> None:
        # 最背面: 画像。描画モードは手書きが最前面、テキスト系はテキストが最前面。
        self.image_label.lower()
        if _is_text_like_tool(self._tool_mode) or self._placement_pending():
            self.ink_overlay.raise_()
            self.text_layer.raise_()
        else:
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
        self._sync_input_routing()

    def _sync_input_routing(self) -> None:
        """テキスト系モード: 手書きレイヤーはマウス透過。配置は text_layer と eventFilter で処理。"""
        is_text_like = _is_text_like_tool(self._tool_mode) or self._placement_pending()
        self.ink_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, is_text_like)
        self.text_layer.setAttribute(Qt.WA_TransparentForMouseEvents, False)

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
        is_text_like = _is_text_like_tool(mode)
        self.ink_overlay.set_tool_mode(mode)
        self.text_layer.set_placement_mode(mode in (TOOL_TEXT, TOOL_PHRASE))
        self.text_layer.set_text_tool_mode(is_text_like)
        if is_text_like:
            self.text_layer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._sync_input_routing()
        self._sync_layer_order()
        self.sync_place_cursor()

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self.ink_overlay.set_brush(color, width, alpha)

    def clear_ink(self) -> None:
        """手書きストロークをすべて消去。"""
        self.ink_overlay.clear_strokes()

    def clear_all_text_boxes(self) -> None:
        """テキストボックスをすべて消去。"""
        self.text_layer.clear_all()
