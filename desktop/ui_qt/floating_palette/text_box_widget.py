"""テキストボックス1件の UI。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, QTimer, QEvent
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPalette,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import DEFAULT_TEXT_STYLE, resolve_text_style

_INNER_PAD_PX = 1
_HANDLE_PX = 12
_DRAG_THRESHOLD_PX = 4
_CORNER_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
}


class _ResizeHandle(QFrame):
    """四隅のサイズ調整グラバー。"""

    resized = Signal(str, QPoint)

    def __init__(self, corner: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._corner = corner
        self.setFixedSize(_HANDLE_PX, _HANDLE_PX)
        self.setCursor(_CORNER_CURSORS.get(corner, Qt.CursorShape.SizeFDiagCursor))
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
            self.resized.emit(self._corner, delta)
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
    editing_finished = Signal(str)
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
        self._editing = False
        self._syncing_text = False
        self._moving = False
        self._move_origin = QPoint()
        self._press_origin: QPoint | None = None
        self._press_moved = False
        self._suppress_focus_check = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self._apply_geometry()
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body = QFrame()
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._body.setMouseTracking(True)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(_INNER_PAD_PX, _INNER_PAD_PX, _INNER_PAD_PX, _INNER_PAD_PX)
        body_lay.setSpacing(0)

        self._editor = QPlainTextEdit(str(box.get("text") or ""))
        self._editor.setObjectName("TextBoxEditor")
        self._editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._editor.setFrameShape(QFrame.NoFrame)
        self._editor.setAutoFillBackground(False)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.viewport().setAutoFillBackground(False)
        self._editor.setViewportMargins(0, 0, 0, 0)
        self._editor.document().setDocumentMargin(0)
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.installEventFilter(self)

        self._display_label = QLabel(str(box.get("text") or ""))
        self._display_label.setObjectName("TextBoxDisplayLabel")
        self._display_label.setWordWrap(True)
        self._display_label.setAutoFillBackground(False)
        self._display_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._display_label.setContentsMargins(0, 0, 0, 0)
        self._display_label.setMouseTracking(True)
        self._display_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._display_label.setStyleSheet(
            "background: transparent; border: none; padding: 0px; margin: 0px;"
        )
        self._display_label.installEventFilter(self)

        self._text_stack = QStackedWidget()
        self._text_stack.setMouseTracking(True)
        self._text_stack.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text_stack.installEventFilter(self)
        self._text_stack.addWidget(self._display_label)
        self._text_stack.addWidget(self._editor)
        self._text_stack.setCurrentWidget(self._display_label)
        body_lay.addWidget(self._text_stack)
        self._body.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._body.installEventFilter(self)
        root.addWidget(self._body)

        self._resize_handles: dict[str, _ResizeHandle] = {}
        for corner in ("tl", "tr", "bl", "br"):
            handle = _ResizeHandle(corner, self)
            handle.resized.connect(self._on_resize_drag)
            handle.hide()
            self._resize_handles[corner] = handle

        self._setup_tight_document()
        self._apply_style()
        self._layout_handles()

    @property
    def box_id(self) -> str:
        return str(self._box.get("id") or "")

    def box_data(self) -> dict[str, Any]:
        self._box["text"] = self._editor.toPlainText()
        return dict(self._box)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        for handle in self._resize_handles.values():
            handle.setVisible(self._selected)
        if not self._selected and self._editing:
            self.finish_editing()
        self._apply_geometry()
        self._apply_style()
        self._layout_handles()
        if self.isVisible():
            self._update_hover_cursor(self.mapFromGlobal(QCursor.pos()))

    def set_display_scale(self, scale: float) -> None:
        self._scale = max(0.01, float(scale))
        self._apply_geometry()
        self._layout_handles()

    def global_frame_rect(self) -> Any:
        return self.frameGeometry().translated(self.mapToGlobal(QPoint(0, 0)).toPoint())

    def start_editing(self) -> None:
        if not self._selected:
            self.selected.emit(self.box_id)
        self._set_editing_mode(True)
        QTimer.singleShot(0, self._focus_editor)

    def finish_editing(self) -> None:
        if not self._editing:
            return
        self._suppress_focus_check = True
        self._editor.clearFocus()
        self._press_origin = None
        self._moving = False
        self._set_editing_mode(False)
        self._suppress_focus_check = False
        self.changed.emit()
        self.editing_finished.emit(self.box_id)

    def _focus_editor(self) -> None:
        if not self._editing:
            return
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self._editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def is_editing(self) -> bool:
        return self._editing

    def append_transcript(self, text: str) -> None:
        chunk = str(text or "").strip()
        if not chunk:
            return
        if not self._editing:
            self.start_editing()
        cur = self._editor.toPlainText()
        new_text = (cur + chunk) if cur else chunk
        self._editor.setPlainText(new_text)
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)
        self._box["text"] = new_text
        self.changed.emit()

    def _set_editing_mode(self, editing: bool) -> None:
        self._editing = bool(editing)
        self._text_stack.setAttribute(Qt.WA_TransparentForMouseEvents, not editing)
        if editing:
            self._text_stack.setCurrentWidget(self._editor)
            self._setup_tight_document()
            self.unsetCursor()
        else:
            text = self._editor.toPlainText()
            self._box["text"] = text
            self._display_label.setText(text)
            self._text_stack.setCurrentWidget(self._display_label)
        self._apply_style()

    def _point_on_handle(self, local_pos: QPoint) -> bool:
        for handle in self._resize_handles.values():
            if handle.isVisible() and handle.geometry().contains(local_pos):
                return True
        return False

    def _update_hover_cursor(self, local_pos: QPoint) -> None:
        if self._editing or self._moving:
            return
        if self._point_on_handle(local_pos):
            self.unsetCursor()
            return
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor:
            if event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, self._check_editing_finished)
            return super().eventFilter(watched, event)

        if self._editing:
            return super().eventFilter(watched, event)

        if watched in (self._body, self._display_label, self._text_stack):
            et = event.type()
            if et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                pos = self.mapFromGlobal(event.globalPosition().toPoint())
                self._update_hover_cursor(pos)
        return super().eventFilter(watched, event)

    def _begin_pointer(self, global_pos: QPoint) -> None:
        if not self._selected:
            self.selected.emit(self.box_id)
        self._press_origin = global_pos
        self._press_moved = False
        self._moving = False

    def _update_move_drag(self, global_pos: QPoint) -> None:
        if self._press_origin is None or self._editing:
            return
        if not self._press_moved:
            delta = global_pos - self._press_origin
            if (
                abs(delta.x()) <= _DRAG_THRESHOLD_PX
                and abs(delta.y()) <= _DRAG_THRESHOLD_PX
            ):
                return
            self._press_moved = True
            self._moving = True
            self._move_origin = global_pos
            return
        if self._moving:
            delta = global_pos - self._move_origin
            self._move_origin = global_pos
            self._on_move_drag(delta)

    def _end_pointer(self) -> None:
        if self._press_moved:
            self.changed.emit()
        self._press_origin = None
        self._press_moved = False
        self._moving = False

    def _check_editing_finished(self) -> None:
        if self._suppress_focus_check or not self._editing:
            return
        fw = QApplication.focusWidget()
        w: QWidget | None = fw
        while w is not None:
            if w is self._editor:
                return
            w = w.parentWidget()
        self._set_editing_mode(False)
        self.changed.emit()
        self.editing_finished.emit(self.box_id)

    def _style(self) -> dict[str, Any]:
        st = self._box.get("style") or {}
        merged = dict(DEFAULT_TEXT_STYLE)
        if isinstance(st, dict):
            merged.update(st)
        return resolve_text_style(merged)

    def _apply_geometry(self) -> None:
        x = int(float(self._box.get("x") or 0) / self._scale)
        y = int(float(self._box.get("y") or 0) / self._scale)
        w = max(40, int(float(self._box.get("width") or 120) / self._scale))
        h = max(24, int(float(self._box.get("height") or 36) / self._scale))
        self.setGeometry(x, y, w, h)

    def _layout_handles(self) -> None:
        w, h = self.width(), self.height()
        s = _HANDLE_PX
        positions = {
            "tl": (0, 0),
            "tr": (max(0, w - s), 0),
            "bl": (0, max(0, h - s)),
            "br": (max(0, w - s), max(0, h - s)),
        }
        for corner, handle in self._resize_handles.items():
            handle.move(*positions[corner])

    def _apply_style(self) -> None:
        st = self._style()
        border = st.get("borderColor") or "#2563eb"
        bw = int(st.get("borderWidth", 2))
        ba = float(st.get("borderAlpha", 1.0))
        fill = st.get("fillColor") or "#ffffff"
        fa = float(st.get("fillAlpha", 0.85))
        tc = st.get("textColor") or "#111827"
        fs = int(st.get("fontSize") or 14)

        bg = "transparent" if fa <= 0 else f"rgba({_hex_rgb(fill)}, {fa})"
        if self._selected:
            border_css = "1px solid #2563eb"
        elif bw > 0 and ba > 0:
            border_css = f"{bw}px solid rgba({_hex_rgb(border)}, {ba})"
        else:
            border_css = "none"

        self._body.setStyleSheet(
            f"QFrame {{ background: {bg}; border: {border_css}; border-radius: 2px; }}"
        )
        font = QFont()
        disp_pt = max(8, int(round(fs / self._scale)))
        font.setPointSize(disp_pt)
        font.setBold(bool(st.get("bold")))
        font.setUnderline(bool(st.get("underline")))
        self._editor.setFont(font)
        self._display_label.setFont(font)
        tc_color = QColor(str(tc))
        pal = self._editor.palette()
        pal.setColor(QPalette.ColorRole.Text, tc_color)
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self._editor.setPalette(pal)
        editor_css = (
            f"QPlainTextEdit#TextBoxEditor {{ color: {tc}; background: transparent; "
            f"border: none; padding: 0px; margin: 0px; }}"
        )
        self._editor.setStyleSheet(editor_css)
        self._editor.viewport().setStyleSheet("background: transparent; border: none;")
        label_css = (
            f"QLabel#TextBoxDisplayLabel {{ color: {tc}; background: transparent; "
            f"border: none; padding: 0px; margin: 0px; }}"
        )
        self._display_label.setStyleSheet(label_css)
        label_pal = self._display_label.palette()
        label_pal.setColor(QPalette.ColorRole.WindowText, tc_color)
        self._display_label.setPalette(label_pal)
        align = str(st.get("align") or "left")
        self._set_editor_alignment(align)
        self._set_label_alignment(align)
        self._sync_display_text()

    def _sync_display_text(self) -> None:
        text = str(
            self._box.get("text") if self._box.get("text") is not None else self._editor.toPlainText()
        )
        if not self._editing:
            self._display_label.setText(text)

    def _setup_tight_document(self) -> None:
        doc = self._editor.document()
        doc.setDocumentMargin(0)
        block_fmt = QTextBlockFormat()
        block_fmt.setTopMargin(0)
        block_fmt.setBottomMargin(0)
        block_fmt.setLineHeight(
            100.0, int(QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        )
        self._editor.blockSignals(True)
        try:
            block = doc.firstBlock()
            while block.isValid():
                cursor = QTextCursor(block)
                cursor.mergeBlockFormat(block_fmt)
                block = block.next()
        finally:
            self._editor.blockSignals(False)

    def _set_label_alignment(self, align: str) -> None:
        if align == "center":
            self._display_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        elif align == "right":
            self._display_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        else:
            self._display_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def _set_editor_alignment(self, align: str) -> None:
        option = self._editor.document().defaultTextOption()
        if align == "center":
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        elif align == "right":
            option.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            option.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._editor.document().setDefaultTextOption(option)

    def apply_style_dict(self, style: dict[str, Any]) -> None:
        merged = {**dict(self._box.get("style") or {}), **style}
        self._box["style"] = resolve_text_style(merged)
        self._apply_style()

    def _on_text_changed(self) -> None:
        if self._syncing_text:
            return
        self._box["text"] = self._editor.toPlainText()
        self.changed.emit()

    def _on_move_drag(self, delta: QPoint) -> None:
        ds = self._scale
        self._box["x"] = float(self._box.get("x") or 0) + delta.x() * ds
        self._box["y"] = float(self._box.get("y") or 0) + delta.y() * ds
        self._apply_geometry()

    def _on_resize_drag(self, corner: str, delta: QPoint) -> None:
        ds = self._scale
        dx = delta.x() * ds
        dy = delta.y() * ds
        x = float(self._box.get("x") or 0)
        y = float(self._box.get("y") or 0)
        w = float(self._box.get("width") or 120)
        h = float(self._box.get("height") or 36)

        if corner == "br":
            w = max(40.0, w + dx)
            h = max(20.0, h + dy)
        elif corner == "bl":
            nw = max(40.0, w - dx)
            x += w - nw
            w = nw
            h = max(20.0, h + dy)
        elif corner == "tr":
            w = max(40.0, w + dx)
            nh = max(20.0, h - dy)
            y += h - nh
            h = nh
        elif corner == "tl":
            nw = max(40.0, w - dx)
            nh = max(20.0, h - dy)
            x += w - nw
            y += h - nh
            w, h = nw, nh

        self._box["x"] = x
        self._box["y"] = y
        self._box["width"] = w
        self._box["height"] = h
        self._apply_geometry()
        self._layout_handles()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._editing:
            if self._point_on_handle(event.position().toPoint()):
                super().mousePressEvent(event)
                return
            self._begin_pointer(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._update_move_drag(event.globalPosition().toPoint())
            if self._moving:
                event.accept()
                return
        self._update_hover_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._end_pointer()
            self._update_hover_cursor(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._point_on_handle(event.position().toPoint()):
            self._press_origin = None
            self._moving = False
            self.selected.emit(self.box_id)
            self.start_editing()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._moving:
            self.unsetCursor()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_handles()


def _hex_rgb(hex_color: str) -> str:
    h = str(hex_color or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"{r},{g},{b}"
