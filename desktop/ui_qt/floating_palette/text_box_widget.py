"""テキストボックス1件の UI。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPalette, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import DEFAULT_TEXT_STYLE, resolve_text_style

_INNER_PAD_PX = 1


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
        self._apply_geometry()
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._body = QFrame()
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(_INNER_PAD_PX, _INNER_PAD_PX, _INNER_PAD_PX, _INNER_PAD_PX)
        body_lay.setSpacing(0)
        self._editor = QPlainTextEdit(str(box.get("text") or ""))
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
        self._display_label.setWordWrap(True)
        self._display_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._display_label.setContentsMargins(0, 0, 0, 0)
        self._display_label.setStyleSheet("background: transparent; border: none; padding: 0px; margin: 0px;")
        self._text_stack = QStackedWidget()
        self._text_stack.addWidget(self._display_label)
        self._text_stack.addWidget(self._editor)
        self._text_stack.setCurrentWidget(self._display_label)
        body_lay.addWidget(self._text_stack)
        outer.addWidget(self._body, 1)

        self._move_bar = _MoveBar(self)
        outer.addWidget(self._move_bar)
        self._move_bar.drag_started.connect(self._on_select_requested)
        self._move_bar.dragged.connect(self._on_move_drag)
        self._move_bar.drag_finished.connect(self._emit_changed)

        self._resize = _ResizeHandle(self)
        self._resize.resized.connect(self._on_resize_drag)
        self._move_bar.hide()
        self._resize.hide()
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
        self._move_bar.setVisible(self._selected)
        self._resize.setVisible(self._selected)
        if not self._selected and self._editing:
            self._set_editing_mode(False)
        self._apply_geometry()
        self._apply_style()
        self._layout_handle()

    def set_display_scale(self, scale: float) -> None:
        self._scale = max(0.01, float(scale))
        self._apply_geometry()
        self._layout_handle()

    def global_frame_rect(self) -> Any:
        return self.frameGeometry().translated(self.mapToGlobal(QPoint(0, 0)).toPoint())

    def start_editing(self) -> None:
        self._set_editing_mode(True)
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._editor.setFocus)

    def is_editing(self) -> bool:
        return self._editing and self._editor.hasFocus()

    def _set_editing_mode(self, editing: bool) -> None:
        self._editing = bool(editing)
        if editing:
            self._text_stack.setCurrentWidget(self._editor)
            self._apply_tight_block_format()
        else:
            text = self._editor.toPlainText()
            self._display_label.setText(text)
            self._text_stack.setCurrentWidget(self._display_label)
        self._apply_style()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor:
            if event.type() == QEvent.Type.FocusIn:
                self.selected.emit(self.box_id)
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, self._check_editing_finished)
        return super().eventFilter(watched, event)

    def _on_select_requested(self) -> None:
        self.selected.emit(self.box_id)

    def _check_editing_finished(self) -> None:
        if self._editing and not self._editor.hasFocus():
            self._set_editing_mode(False)
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
        chrome = 14 if self._selected else 0
        self.setGeometry(x, y, w, h + chrome)

    def _layout_handle(self) -> None:
        if self._selected:
            self._resize.move(
                self.width() - self._resize.width(),
                self.height() - self._resize.height() - 14,
            )
        else:
            self._resize.move(self.width() - self._resize.width(), self.height() - self._resize.height())

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
        self._editor.setStyleSheet(
            f"QPlainTextEdit {{ color: {tc}; background: transparent; border: none; "
            f"padding: 0px; margin: 0px; }}"
        )
        self._display_label.setStyleSheet(
            f"color: {tc}; background: transparent; border: none; padding: 0px; margin: 0px;"
        )
        align = str(st.get("align") or "left")
        self._set_editor_alignment(align)
        self._set_label_alignment(align)
        self._apply_tight_block_format()
        if not self._editing:
            self._display_label.setText(self._editor.toPlainText())

    def _apply_tight_block_format(self) -> None:
        doc = self._editor.document()
        doc.setDocumentMargin(0)
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        block_fmt = QTextBlockFormat()
        block_fmt.setTopMargin(0)
        block_fmt.setBottomMargin(0)
        block_fmt.setLineHeight(100, QTextBlockFormat.LineHeightTypes.ProportionalHeight)
        cursor.mergeBlockFormat(block_fmt)
        cursor.clearSelection()

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
        self._box["text"] = self._editor.toPlainText()
        self._apply_tight_block_format()
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
            if self._selected and not self._editing:
                self.start_editing()
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
