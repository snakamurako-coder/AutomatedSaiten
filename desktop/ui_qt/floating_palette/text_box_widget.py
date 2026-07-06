"""テキストボックス1件の UI（移動・ダブルクリック編集・自動サイズ）。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, QTimer, QEvent
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
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

from models.text_annotation_repo import TEXT_STYLE_TEMPLATE_A, resolve_text_style

_FIXED_FONT_PT = 14
_DRAG_THRESHOLD_PX = 4
_MIN_NATIVE_W = 32.0
_MIN_NATIVE_H = 18.0


class TextBoxWidget(QFrame):
    """選択・移動・編集可能なテキストボックス。"""

    changed = Signal()
    selected = Signal(str)
    editing_finished = Signal(str)

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
        self._text_tool_mode = False
        self._moving = False
        self._move_origin = QPoint()
        self._press_origin: QPoint | None = None
        self._press_moved = False
        self._suppress_focus_check = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body = QFrame()
        self._body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._body.setMouseTracking(True)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._editor = QPlainTextEdit(str(box.get("text") or ""))
        self._editor.setObjectName("TextBoxEditor")
        self._editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._editor.setFrameShape(QFrame.NoFrame)
        self._editor.setAutoFillBackground(False)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.viewport().setAutoFillBackground(False)
        self._editor.setViewportMargins(0, 0, 0, 0)
        self._editor.document().setDocumentMargin(0)
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.installEventFilter(self)

        self._display_label = QLabel(str(box.get("text") or ""))
        self._display_label.setObjectName("TextBoxDisplayLabel")
        self._display_label.setWordWrap(False)
        self._display_label.setAutoFillBackground(False)
        self._display_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._display_label.setContentsMargins(0, 0, 0, 0)
        self._display_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._display_label.setStyleSheet(
            "background: transparent; border: none; padding: 0px; margin: 0px;"
        )

        self._text_stack = QStackedWidget()
        self._text_stack.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text_stack.addWidget(self._display_label)
        self._text_stack.addWidget(self._editor)
        self._text_stack.setCurrentWidget(self._display_label)
        body_lay.addWidget(self._text_stack)
        self._body.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(self._body)

        self._setup_tight_document()
        self._apply_style()
        self._fit_to_content()

    @property
    def box_id(self) -> str:
        return str(self._box.get("id") or "")

    def box_data(self) -> dict[str, Any]:
        self._box["text"] = self._editor.toPlainText()
        self._fit_to_content()
        data = copy.deepcopy(self._box)
        if isinstance(data.get("style"), dict):
            data["style"] = resolve_text_style(data["style"])
        return data

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        if not self._selected and self._editing:
            self.finish_editing()
        self._apply_style()

    def set_display_scale(self, scale: float) -> None:
        self._scale = max(0.01, float(scale))
        self._apply_style()
        self._fit_to_content()

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

    def is_editing(self) -> bool:
        return self._editing

    def set_text_tool_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._text_tool_mode == enabled:
            return
        self._text_tool_mode = enabled
        self._apply_style()

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
        self._fit_to_content()
        self.changed.emit()

    def apply_style_dict(self, style: dict[str, Any]) -> None:
        merged = {**dict(self._box.get("style") or {}), **style}
        self._box["style"] = resolve_text_style(merged)
        self._apply_style()

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
        self._fit_to_content()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor and event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._check_editing_finished)
        return super().eventFilter(watched, event)

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
        merged = dict(TEXT_STYLE_TEMPLATE_A)
        if isinstance(st, dict):
            merged.update(st)
        return resolve_text_style(merged)

    def _apply_geometry(self) -> None:
        x = int(float(self._box.get("x") or 0) / self._scale)
        y = int(float(self._box.get("y") or 0) / self._scale)
        w = max(8, int(float(self._box.get("width") or _MIN_NATIVE_W) / self._scale))
        h = max(8, int(float(self._box.get("height") or _MIN_NATIVE_H) / self._scale))
        self.setGeometry(x, y, w, h)

    def _content_font(self) -> QFont:
        font = QFont()
        disp_pt = max(8, int(round(_FIXED_FONT_PT / self._scale)))
        font.setPointSize(disp_pt)
        return font

    def _fit_to_content(self) -> None:
        font = self._content_font()
        fm = QFontMetrics(font)
        text = self._editor.toPlainText() if self._editing else self._display_label.text()
        lines = text.split("\n") if text else [""]
        line_h = fm.height()
        max_w = max((fm.horizontalAdvance(line) for line in lines), default=fm.horizontalAdvance(" "))
        self._box["width"] = max(_MIN_NATIVE_W, float(max_w))
        self._box["height"] = max(_MIN_NATIVE_H, float(line_h * max(1, len(lines))))
        self._apply_geometry()

    def _apply_style(self) -> None:
        st = self._style()
        fa = float(st.get("fillAlpha", 0))
        fill = st.get("fillColor") or "#ffffff"
        tc = st.get("textColor") or "#111827"

        bg = "transparent" if fa <= 0 else f"rgba({_hex_rgb(fill)}, {fa})"
        if self._text_tool_mode:
            border_css = (
                "1px solid #2563eb"
                if self._selected or self._editing
                else "1px dashed rgba(37, 99, 235, 0.55)"
            )
        else:
            border_css = "none"

        self._body.setStyleSheet(
            f"QFrame {{ background: {bg}; border: {border_css}; border-radius: 0px; }}"
        )
        font = self._content_font()
        self._editor.setFont(font)
        self._display_label.setFont(font)
        tc_color = QColor(str(tc))
        pal = self._editor.palette()
        pal.setColor(QPalette.ColorRole.Text, tc_color)
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self._editor.setPalette(pal)
        css = (
            f"QPlainTextEdit#TextBoxEditor {{ color: {tc}; background: transparent; "
            f"border: none; padding: 0px; margin: 0px; }}"
        )
        self._editor.setStyleSheet(css)
        self._editor.viewport().setStyleSheet("background: transparent; border: none;")
        label_css = (
            f"QLabel#TextBoxDisplayLabel {{ color: {tc}; background: transparent; "
            f"border: none; padding: 0px; margin: 0px; }}"
        )
        self._display_label.setStyleSheet(label_css)
        label_pal = self._display_label.palette()
        label_pal.setColor(QPalette.ColorRole.WindowText, tc_color)
        self._display_label.setPalette(label_pal)
        self._display_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        option = self._editor.document().defaultTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._editor.document().setDefaultTextOption(option)
        if not self._editing:
            self._display_label.setText(
                str(self._box.get("text") if self._box.get("text") is not None else self._editor.toPlainText())
            )

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

    def _on_text_changed(self) -> None:
        self._box["text"] = self._editor.toPlainText()
        self._fit_to_content()
        self.changed.emit()

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
            ds = self._scale
            self._box["x"] = float(self._box.get("x") or 0) + delta.x() * ds
            self._box["y"] = float(self._box.get("y") or 0) + delta.y() * ds
            self._apply_geometry()

    def _end_pointer(self) -> None:
        if self._press_moved:
            self.changed.emit()
        self._press_origin = None
        self._press_moved = False
        self._moving = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._editing:
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
        if not self._editing:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._end_pointer()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._editing:
            self.selected.emit(self.box_id)
            self.start_editing()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._moving:
            self.unsetCursor()
        super().leaveEvent(event)


def _hex_rgb(hex_color: str) -> str:
    h = str(hex_color or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f"{r},{g},{b}"
