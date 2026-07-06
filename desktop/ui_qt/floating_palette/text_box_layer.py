"""クロップ上のテキストボックスレイヤー。"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPointF, Qt, Signal, QEvent, QTimer
from PySide6.QtGui import QMouseEvent, QTabletEvent
from PySide6.QtWidgets import QFrame, QWidget

from models.text_annotation_repo import new_text_box
from ui_qt.floating_palette.text_box_widget import TextBoxWidget
from ui_qt.stylus_overlay import is_pen_mouse_event, is_stylus_tablet_event

_MIN_NATIVE_W = 40.0
_MIN_NATIVE_H = 24.0
_MIN_DISPLAY_DRAG_PX = 6


class TextBoxLayer(QWidget):
    """記述欄クロップ上のテキストボックス群。"""

    annotations_changed = Signal()
    selection_changed = Signal(object)  # box dict | None
    editing_finished = Signal()

    def __init__(
        self,
        *,
        native_w: int,
        native_h: int,
        annotations: list[dict[str, Any]] | None = None,
        on_changed: Callable[[list[dict[str, Any]]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._native_w = max(1, int(native_w))
        self._native_h = max(1, int(native_h))
        self._display_w = self._native_w
        self._display_h = self._native_h
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._on_changed = on_changed
        self._annotations: list[dict[str, Any]] = list(annotations or [])
        self._widgets: dict[str, TextBoxWidget] = {}
        self._selected_id: str | None = None
        self._placement_mode = False
        self._show_text = True
        self._text_tool_mode = False
        self._palm_rejection = True
        self._placing = False
        self._place_origin: QPointF | None = None
        self._rubber = QFrame(self)
        self._rubber.setStyleSheet(
            "QFrame { background: rgba(37, 99, 235, 0.12);"
            " border: 1px dashed #2563eb; }"
        )
        self._rubber.hide()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_TabletTracking, True)
        self.setMouseTracking(True)
        self.setFixedSize(self._display_w, self._display_h)
        self._rebuild_widgets()

    def set_display_size(self, w: int, h: int) -> None:
        self._display_w = max(1, int(w))
        self._display_h = max(1, int(h))
        self._scale_x = self._native_w / float(self._display_w)
        self._scale_y = self._native_h / float(self._display_h)
        self.setFixedSize(self._display_w, self._display_h)
        for wid in self._widgets.values():
            wid.set_display_scale(self._scale_x)
        self._rebuild_widgets()

    def set_placement_mode(self, enabled: bool) -> None:
        self._placement_mode = bool(enabled)
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._cancel_place_drag()
            self.unsetCursor()

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)

    def set_show_text(self, visible: bool) -> None:
        self._show_text = bool(visible)
        self._apply_layer_visibility()

    def set_text_tool_mode(self, enabled: bool) -> None:
        self._text_tool_mode = bool(enabled)
        self._apply_layer_visibility()
        for w in self._widgets.values():
            w.set_text_tool_mode(enabled)

    def _apply_layer_visibility(self) -> None:
        self.setVisible(self._show_text or self._text_tool_mode)

    def annotations(self) -> list[dict[str, Any]]:
        self._sync_annotations_from_widgets()
        return copy.deepcopy(self._annotations)

    def set_annotations(self, items: list[dict[str, Any]]) -> None:
        self._annotations = copy.deepcopy(items or [])
        self._rebuild_widgets(from_widgets=False)

    def _sync_annotations_from_widgets(self) -> None:
        if not self._widgets:
            return
        order: list[str] = []
        seen: set[str] = set()
        for item in self._annotations:
            bid = str(item.get("id") or "")
            if bid and bid in self._widgets and bid not in seen:
                order.append(bid)
                seen.add(bid)
        for bid in self._widgets:
            if bid not in seen:
                order.append(bid)
                seen.add(bid)
        self._annotations = [
            copy.deepcopy(self._widgets[bid].box_data())
            for bid in order
            if bid in self._widgets
        ]

    def selected_box(self) -> dict[str, Any] | None:
        if not self._selected_id:
            return None
        w = self._widgets.get(self._selected_id)
        return w.box_data() if w else None

    def has_editing_focus(self) -> bool:
        return any(w.is_editing() for w in self._widgets.values())

    def point_on_any_box(self, display_x: int, display_y: int) -> bool:
        return self.childAt(display_x, display_y) is not None

    def select_box(self, box_id: str | None) -> None:
        self._selected_id = box_id or None
        for bid, w in self._widgets.items():
            w.set_selected(bid == self._selected_id)
        self.selection_changed.emit(self.selected_box())

    def clear_selection(self) -> None:
        self.select_box(None)

    def update_selected_style(self, style: dict[str, Any]) -> None:
        if not self._selected_id:
            return
        w = self._widgets.get(self._selected_id)
        if w:
            w.apply_style_dict(style)
            self._notify_changed()

    def edit_selected(self, *, caret_at_end: bool = False) -> None:
        if self._selected_id and self._selected_id in self._widgets:
            self._widgets[self._selected_id].start_editing(caret_at_end=caret_at_end)

    def focus_selected_caret_at_end(self) -> bool:
        if not self._selected_id:
            return False
        w = self._widgets.get(self._selected_id)
        if w is None:
            return False
        return w.focus_caret_at_end()

    def prepare_selected_speech_input(self) -> bool:
        if not self._selected_id:
            return False
        w = self._widgets.get(self._selected_id)
        if w is None:
            return False
        return w.prepare_speech_input()

    def release_selected_speech_input_guard(self) -> None:
        if not self._selected_id:
            return
        w = self._widgets.get(self._selected_id)
        if w is not None:
            w.release_speech_input_guard()

    def is_selected_editor_focused_at_end(self) -> bool:
        if not self._selected_id:
            return False
        w = self._widgets.get(self._selected_id)
        return w is not None and w.is_editor_focused_at_end()

    def finish_all_editing(self) -> None:
        for w in self._widgets.values():
            w.finish_editing()

    def append_transcript_to_selected(self, text: str) -> bool:
        if not self._selected_id:
            return False
        w = self._widgets.get(self._selected_id)
        if w is None:
            return False
        w.append_transcript(text)
        return True

    def delete_selected(self) -> None:
        deleted_id = self._selected_id
        if not deleted_id:
            return
        self.finish_all_editing()
        self._sync_annotations_from_widgets()
        self._annotations = [
            a for a in self._annotations if str(a.get("id") or "") != deleted_id
        ]
        self._selected_id = None
        self._rebuild_widgets(from_widgets=False)
        self._persist_annotations()
        self.selection_changed.emit(None)

    def clear_all(self) -> None:
        """画像上のテキストボックスをすべて削除。"""
        if not self._annotations and not self._widgets:
            return
        self.finish_all_editing()
        self._annotations = []
        self._selected_id = None
        self._rebuild_widgets(from_widgets=False)
        self._persist_annotations()
        self.selection_changed.emit(None)

    def is_placing(self) -> bool:
        return self._placing

    def handle_placement_event(self, et: QEvent.Type, local_pos: QPointF, event) -> bool:
        """テキスト配置ドラッグ（container / ink からの転送も含む）。"""
        if not self._placement_mode:
            return False
        if self._reject_placement_event(event):
            return False

        lx, ly = int(local_pos.x()), int(local_pos.y())

        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.TabletPress):
            if self._placing:
                return True
            child = self.childAt(lx, ly)
            if child is not None and child is not self._rubber:
                return False
            self._begin_place_drag(local_pos)
            return True

        if et in (QEvent.Type.MouseMove, QEvent.Type.TabletMove):
            if not self._placing:
                return False
            self._update_place_drag(local_pos)
            return True

        if et in (QEvent.Type.MouseButtonRelease, QEvent.Type.TabletRelease):
            if not self._placing:
                return False
            self._finish_place_drag(local_pos)
            return True

        return False

    def _reject_placement_event(self, event) -> bool:
        if not self._palm_rejection:
            return False
        if isinstance(event, QMouseEvent) and is_pen_mouse_event(event):
            return True
        if isinstance(event, QTabletEvent) and is_stylus_tablet_event(event):
            return True
        return False

    def _begin_place_drag(self, pos: QPointF) -> None:
        self.finish_all_editing()
        self.clear_selection()
        self._placing = True
        self._place_origin = QPointF(pos)
        self._update_place_drag(pos)
        self._rubber.show()
        self._rubber.raise_()
        self.grabMouse()

    def _update_place_drag(self, pos: QPointF) -> None:
        if self._place_origin is None:
            return
        x1, y1 = self._place_origin.x(), self._place_origin.y()
        x2, y2 = pos.x(), pos.y()
        left = min(x1, x2)
        top = min(y1, y2)
        w = max(1.0, abs(x2 - x1))
        h = max(1.0, abs(y2 - y1))
        self._rubber.setGeometry(int(left), int(top), int(w), int(h))

    def _finish_place_drag(self, pos: QPointF) -> None:
        if self._place_origin is None:
            self._cancel_place_drag()
            return
        origin = self._place_origin
        self._cancel_place_drag()
        self.place_box_rect(origin.x(), origin.y(), pos.x(), pos.y())

    def _cancel_place_drag(self) -> None:
        if self._placing:
            self.releaseMouse()
        self._placing = False
        self._place_origin = None
        self._rubber.hide()

    def place_box_rect(
        self,
        display_x1: float,
        display_y1: float,
        display_x2: float,
        display_y2: float,
    ) -> dict[str, Any]:
        left = min(display_x1, display_x2)
        top = min(display_y1, display_y2)
        dw = abs(display_x2 - display_x1)
        dh = abs(display_y2 - display_y1)
        if dw < _MIN_DISPLAY_DRAG_PX and dh < _MIN_DISPLAY_DRAG_PX:
            dw = 80.0
            dh = 28.0
        nw = max(_MIN_NATIVE_W, dw * self._scale_x)
        nh = max(_MIN_NATIVE_H, dh * self._scale_y)
        nx = max(0.0, min(self._native_w - nw, left * self._scale_x))
        ny = max(0.0, min(self._native_h - nh, top * self._scale_y))
        box = new_text_box(nx, ny, width=nw, height=nh)
        self._sync_annotations_from_widgets()
        self._annotations.append(copy.deepcopy(box))
        self._rebuild_widgets(from_widgets=False)
        self.select_box(str(box["id"]))
        w = self._widgets.get(str(box["id"]))
        if w:
            QTimer.singleShot(0, w.start_editing)
        self._persist_annotations()
        return self._widgets[str(box["id"])].box_data() if str(box["id"]) in self._widgets else box

    def _rebuild_widgets(self, *, from_widgets: bool = True) -> None:
        if from_widgets:
            self._sync_annotations_from_widgets()
        for w in list(self._widgets.values()):
            w.blockSignals(True)
            w.setParent(None)
            w.deleteLater()
        self._widgets.clear()
        scale = self._scale_x
        for item in self._annotations:
            bid = str(item.get("id") or "")
            if not bid:
                continue
            item_copy = copy.deepcopy(item)
            w = TextBoxWidget(item_copy, display_scale=scale, parent=self)
            w.set_text_tool_mode(self._text_tool_mode)
            w.changed.connect(self._notify_changed)
            w.selected.connect(self._on_widget_selected)
            w.editing_finished.connect(self._on_widget_editing_finished)
            w.set_selected(bid == self._selected_id)
            w.show()
            w.raise_()
            self._widgets[bid] = w
        self._rubber.raise_()

    def _on_widget_selected(self, box_id: str) -> None:
        self.select_box(box_id)

    def _on_widget_editing_finished(self, _box_id: str) -> None:
        if not self.has_editing_focus():
            self.editing_finished.emit()

    def _notify_changed(self) -> None:
        self._sync_annotations_from_widgets()
        self._persist_annotations()

    def _persist_annotations(self) -> None:
        self.annotations_changed.emit()
        if self._on_changed:
            self._on_changed(copy.deepcopy(self._annotations))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placement_mode and event.button() == Qt.LeftButton:
            if self.handle_placement_event(
                QEvent.Type.MouseButtonPress, event.position(), event
            ):
                event.accept()
                return
        if event.button() == Qt.LeftButton and not self._placing:
            child = self.childAt(int(event.position().x()), int(event.position().y()))
            if child is not None and child is not self._rubber:
                super().mousePressEvent(event)
                return
            self.finish_all_editing()
            self.clear_selection()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placing:
            if self.handle_placement_event(QEvent.Type.MouseMove, event.position(), event):
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placing and event.button() == Qt.LeftButton:
            if self.handle_placement_event(
                QEvent.Type.MouseButtonRelease, event.position(), event
            ):
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        et = event.type()
        if self._placement_mode and et in (
            QEvent.Type.TabletPress,
            QEvent.Type.TabletMove,
            QEvent.Type.TabletRelease,
        ):
            if self.handle_placement_event(et, event.position(), event):
                event.accept()
                return
        super().tabletEvent(event)
