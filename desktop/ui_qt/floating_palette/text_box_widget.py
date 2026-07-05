"""テキストボックス1件の UI。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import DEFAULT_TEXT_STYLE


class _MoveBar(QFrame):
    drag_started = Signal()
    dragged = Signal(QPoint)
    drag_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setStyleSheet("background: #2563eb; border: none; border-radius: 3px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lbl = QLabel("⋮⋮")
        lbl.setStyleSheet("color: white; font-size: 10px; border: none; background: transparent;")
        lay.addWidget(lbl)
        self._origin = QPoint()
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._origin = event.globalPosition().toPoint()
            self.drag_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._origin
            self._origin = event.globalPosition().toPoint()
            self.dragged.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ResizeHandle(QFrame):
    resized = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet(
            "background: #2563eb; border: 1px solid white; border-radius: 2px;"
        )
        self._origin = QPoint()
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._origin
            self._origin = event.globalPosition().toPoint()
            self.resized.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        event.accept()


class TextBoxWidget(QFrame):
    """選択可能なテキストボックス（移動・リサイズ・編集）。"""

    changed = Signal()
    selected = Signal(str)
    request_edit = Signal(str)
    request_delete = Signal(str)

    def __init__(
        self,
        box: dict[str, Any],
        *,
        display_scale: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._box = box
        self._scale = max(0.01, float(display_scale))
        self._selected = False
        self._apply_geometry()
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._body = QFrame()
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(4, 4, 4, 4)
        self._editor = QPlainTextEdit(str(box.get("text") or ""))
        self._editor.setFrameShape(QFrame.NoFrame)
        self._editor.textChanged.connect(self._on_text_changed)
        body_lay.addWidget(self._editor)
        outer.addWidget(self._body, 1)

        self._move_bar = _MoveBar(self)
        outer.addWidget(self._move_bar)
        self._move_bar.dragged.connect(self._on_move_drag)
        self._move_bar.drag_finished.connect(self._emit_changed)

        self._resize = _ResizeHandle(self)
        self._resize.resized.connect(self._on_resize_drag)
        self._apply_style()
        self._layout_handle()

    @property
    def box_id(self) -> str:
        return str(self._box.get("id") or "")

    def box_data(self) -> dict[str, Any]:
        self._box["text"] = self._editor.toPlainText()
        return dict(self._box)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self._apply_style()

    def set_display_scale(self, scale: float) -> None:
        self._scale = max(0.01, float(scale))
        self._apply_geometry()
        self._layout_handle()

    def global_frame_rect(self) -> Any:
        return self.frameGeometry().translated(self.mapToGlobal(QPoint(0, 0)).toPoint())

    def start_editing(self) -> None:
        self._editor.setFocus()

    def _style(self) -> dict[str, Any]:
        st = self._box.get("style") or {}
        merged = dict(DEFAULT_TEXT_STYLE)
        if isinstance(st, dict):
            merged.update(st)
        return merged

    def _apply_geometry(self) -> None:
        x = int(float(self._box.get("x") or 0) / self._scale)
        y = int(float(self._box.get("y") or 0) / self._scale)
        w = max(40, int(float(self._box.get("width") or 120) / self._scale))
        h = max(24, int(float(self._box.get("height") or 36) / self._scale))
        self.setGeometry(x, y, w, h + 14)

    def _layout_handle(self) -> None:
        self._resize.move(self.width() - self._resize.width(), self.height() - self._resize.height() - 14)

    def _apply_style(self) -> None:
        st = self._style()
        border = st.get("borderColor") or "#2563eb"
        bw = int(st.get("borderWidth") or 2)
        fill = st.get("fillColor") or "#ffffff"
        fa = float(st.get("fillAlpha") or 0.85)
        tc = st.get("textColor") or "#111827"
        fs = int(st.get("fontSize") or 14)
        sel = "2px solid #2563eb" if self._selected else f"{bw}px solid {border}"
        self._body.setStyleSheet(
            f"QFrame {{ background: rgba({_hex_rgb(fill)}, {fa});"
            f" border: {sel}; border-radius: 4px; }}"
        )
        font = QFont()
        font.setPointSize(max(8, int(fs / max(1.0, self._scale * 0.5))))
        font.setBold(bool(st.get("bold")))
        font.setUnderline(bool(st.get("underline")))
        self._editor.setFont(font)
        self._editor.setStyleSheet(f"color: {tc}; background: transparent; border: none;")
        align = str(st.get("align") or "left")
        if align == "center":
            self._editor.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        elif align == "right":
            self._editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            self._editor.setAlignment(Qt.AlignmentFlag.AlignLeft)

    def apply_style_dict(self, style: dict[str, Any]) -> None:
        self._box["style"] = {**self._style(), **style}
        self._apply_style()

    def _on_text_changed(self) -> None:
        self._box["text"] = self._editor.toPlainText()
        self.changed.emit()

    def _on_move_drag(self, delta: QPoint) -> None:
        ds = self._scale
        self._box["x"] = float(self._box.get("x") or 0) + delta.x() * ds
        self._box["y"] = float(self._box.get("y") or 0) + delta.y() * ds
        self._apply_geometry()

    def _on_resize_drag(self, delta: QPoint) -> None:
        ds = self._scale
        self._box["width"] = max(40.0, float(self._box.get("width") or 120) + delta.x() * ds)
        self._box["height"] = max(20.0, float(self._box.get("height") or 36) + delta.y() * ds)
        self._apply_geometry()
        self._layout_handle()

    def _emit_changed(self) -> None:
        self.changed.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.box_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_handle()


def _hex_rgb(hex_color: str) -> str:
    h = str(hex_color or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"{r},{g},{b}"
