"""クロップ上のテキストボックスレイヤー。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal, QEvent, QTimer
from PySide6.QtGui import QMouseEvent, QTabletEvent
from PySide6.QtWidgets import QWidget

from models.text_annotation_repo import new_text_box
from ui_qt.floating_palette.text_box_widget import TextBoxWidget
from ui_qt.stylus_overlay import is_stylus_tablet_event


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
        self._palm_rejection = True
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
            self.unsetCursor()

    def set_palm_rejection(self, enabled: bool) -> None:
        self._palm_rejection = bool(enabled)

    def set_show_text(self, visible: bool) -> None:
        self._show_text = bool(visible)
        self.setVisible(self._show_text)

    def annotations(self) -> list[dict[str, Any]]:
        return [w.box_data() for w in self._widgets.values()]

    def set_annotations(self, items: list[dict[str, Any]]) -> None:
        self._annotations = list(items or [])
        self._rebuild_widgets()

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

    def edit_selected(self) -> None:
        if self._selected_id and self._selected_id in self._widgets:
            self._widgets[self._selected_id].start_editing()

    def delete_selected(self) -> None:
        if not self._selected_id:
            return
        self._annotations = [a for a in self._annotations if str(a.get("id")) != self._selected_id]
        self._selected_id = None
        self._rebuild_widgets()
        self._notify_changed()
        self.selection_changed.emit(None)

    def place_box_at(self, display_x: float, display_y: float) -> dict[str, Any]:
        nx = max(0.0, min(self._native_w, display_x * self._scale_x))
        ny = max(0.0, min(self._native_h, display_y * self._scale_y))
        box = new_text_box(nx, ny)
        self._annotations.append(box)
        self._rebuild_widgets()
        self.select_box(str(box["id"]))
        w = self._widgets.get(str(box["id"]))
        if w:
            QTimer.singleShot(0, w.start_editing)
        self._notify_changed()
        return box

    def _rebuild_widgets(self) -> None:
        if self._widgets:
            self._annotations = self.annotations()
        for w in list(self._widgets.values()):
            w.setParent(None)
            w.deleteLater()
        self._widgets.clear()
        scale = self._scale_x
        for item in self._annotations:
            bid = str(item.get("id") or "")
            if not bid:
                continue
            w = TextBoxWidget(item, display_scale=scale, parent=self)
            w.changed.connect(self._notify_changed)
            w.selected.connect(self._on_widget_selected)
            w.editing_finished.connect(self._on_widget_editing_finished)
            w.set_selected(bid == self._selected_id)
            self._widgets[bid] = w

    def _on_widget_selected(self, box_id: str) -> None:
        self.select_box(box_id)

    def _on_widget_editing_finished(self, _box_id: str) -> None:
        if not self.has_editing_focus():
            self.editing_finished.emit()

    def _notify_changed(self) -> None:
        self._annotations = self.annotations()
        self.annotations_changed.emit()
        if self._on_changed:
            self._on_changed(self._annotations)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placement_mode and event.button() == Qt.LeftButton:
            pos = event.position()
            lx, ly = int(pos.x()), int(pos.y())
            if self.childAt(lx, ly) is None:
                self.place_box_at(pos.x(), pos.y())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self.clear_selection()
            event.accept()
            return
        super().mousePressEvent(event)

    def tabletEvent(self, event: QTabletEvent) -> None:  # noqa: N802
        if self._palm_rejection and is_stylus_tablet_event(event):
            event.ignore()
            return
        if self._placement_mode and event.type() == QEvent.Type.TabletPress:
            pos = event.position()
            lx, ly = int(pos.x()), int(pos.y())
            if self.childAt(lx, ly) is None:
                self.place_box_at(pos.x(), pos.y())
            event.accept()
            return
        super().tabletEvent(event)
