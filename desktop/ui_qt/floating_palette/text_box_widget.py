"""テキストボックス1件の UI（移動・ダブルクリック編集）。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, QTimer, QEvent
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPalette,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    DEFAULT_TEXT_COLOR,
    DEFAULT_TEXT_STYLE,
    TEXT_STYLE_TEMPLATE_A,
    resolve_text_style,
)
from ui_qt.floating_palette.text_rich import (
    TEXT_FORMAT_HTML,
    box_text_html,
    html_body_for_label,
    mark_box_html,
    normalize_text_align,
    qt_horizontal_alignment,
    qt_label_alignment,
    sync_box_html_from_style,
)

_DEFAULT_FONT_PT = int(DEFAULT_TEXT_STYLE.get("fontSize") or 14)
_DEFAULT_LINE_SPACING_PT = int(DEFAULT_TEXT_STYLE.get("lineSpacing") or 20)
_DRAG_THRESHOLD_PX = 4
_MIN_NATIVE_W = 32.0
_MIN_NATIVE_H = 18.0
_HANDLE_SIZE = 6
_HANDLE_OVERHANG = 3

_CORNER_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
}


class _CornerHandle(QFrame):
    """選択時に四隅へ表示するリサイズ用グラバー。"""

    def __init__(self, corner: str, owner: "TextBoxWidget") -> None:
        super().__init__(owner)
        self._corner = corner
        self._owner = owner
        self.setFixedSize(_HANDLE_SIZE, _HANDLE_SIZE)
        self.setCursor(_CORNER_CURSORS[corner])
        self.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #2563eb; border-radius: 0px; }"
        )
        self.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._owner._begin_resize(self._corner, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._owner._update_resize(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._owner._end_resize()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TextBoxWidget(QFrame):
    """選択・移動・編集可能なテキストボックス。"""

    changed = Signal()
    selected = Signal(str)
    editing_finished = Signal(str)
    interactive_change_started = Signal()
    interactive_change_finished = Signal()
    editing_started = Signal()
    editing_committed = Signal()
    char_format_state_changed = Signal(dict)

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
        self._preview_mode = False
        self._preview_resize = False
        self._focus_guard_widgets: list[QWidget] = []
        self._moving = False
        self._resizing = False
        self._resize_corner: str | None = None
        self._resize_origin = QPoint()
        self._resize_orig_box: tuple[float, float, float, float] | None = None
        self._move_origin = QPoint()
        self._press_origin: QPoint | None = None
        self._press_moved = False
        self._suppress_focus_check = False
        self._syncing_format_ui = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        self._body = QFrame(self)
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._body.setMouseTracking(True)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._editor = QTextEdit()
        self._editor.setObjectName("TextBoxEditor")
        self._editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._editor.setAcceptRichText(True)
        self._editor.setFrameShape(QFrame.NoFrame)
        self._editor.setAutoFillBackground(False)
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.viewport().setAutoFillBackground(False)
        self._editor.setViewportMargins(0, 0, 0, 0)
        self._editor.document().setDocumentMargin(0)
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.cursorPositionChanged.connect(self._emit_char_format_state)
        self._editor.selectionChanged.connect(self._emit_char_format_state)
        self._editor.installEventFilter(self)

        self._display_label = QLabel()
        self._display_label.setObjectName("TextBoxDisplayLabel")
        self._display_label.setTextFormat(Qt.TextFormat.RichText)
        self._display_label.setWordWrap(True)
        self._display_label.setAutoFillBackground(False)
        self._display_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._display_label.setContentsMargins(0, 0, 0, 0)
        self._display_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._display_label.setStyleSheet(
            "background: transparent; border: none; padding: 0px; margin: 0px;"
        )

        self._text_stack = QStackedWidget()
        self._text_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._text_stack.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text_stack.addWidget(self._display_label)
        self._text_stack.addWidget(self._editor)
        self._text_stack.setCurrentWidget(self._display_label)
        body_lay.addWidget(self._text_stack, 1)

        self._load_editor_content()

        self._handles: dict[str, _CornerHandle] = {
            corner: _CornerHandle(corner, self) for corner in ("tl", "tr", "bl", "br")
        }

        self._setup_tight_document()
        self._apply_style()
        self._apply_geometry()
        self._update_handles()

    @property
    def box_id(self) -> str:
        return str(self._box.get("id") or "")

    def box_data(self) -> dict[str, Any]:
        self._sync_editor_to_box()
        data = copy.deepcopy(self._box)
        if isinstance(data.get("style"), dict):
            data["style"] = resolve_text_style(data["style"])
        return data

    def current_char_format_state(self) -> dict[str, Any]:
        st = self._style()
        cursor = self._editor.textCursor()
        fmt = cursor.charFormat() if cursor.hasSelection() else self._editor.currentCharFormat()
        color = fmt.foreground().color()
        tc = color.name(QColor.NameFormat.HexRgb) if color.isValid() else str(st.get("textColor"))
        pt = fmt.fontPointSize()
        if pt <= 0:
            pt = float(st.get("fontSize") or _DEFAULT_FONT_PT)
        block_fmt = cursor.blockFormat()
        lh = float(block_fmt.lineHeight())
        lh_type = block_fmt.lineHeightType()
        if (
            lh > 0
            and lh_type == int(QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        ):
            line_spacing = int(round(lh))
        else:
            line_spacing = int(
                round(
                    float(
                        st.get("lineSpacing")
                        or _DEFAULT_LINE_SPACING_PT
                        or st.get("fontSize")
                        or _DEFAULT_FONT_PT
                    )
                )
            )
        return {
            "color": tc,
            "fontSize": int(round(pt)),
            "lineSpacing": line_spacing,
            "bold": fmt.fontWeight() >= int(QFont.Weight.Bold),
            "italic": fmt.fontItalic(),
            "underline": fmt.fontUnderline(),
        }

    def apply_char_format(self, changes: dict[str, Any]) -> None:
        if not self._editing:
            self.start_editing(record_undo=False)
        cursor = self._editor.textCursor()
        fmt = QTextCharFormat()
        if "color" in changes:
            fmt.setForeground(QColor(str(changes["color"])))
        if "fontSize" in changes:
            fmt.setFontPointSize(max(6.0, float(changes["fontSize"])))
        if "bold" in changes:
            fmt.setFontWeight(
                QFont.Weight.Bold if changes["bold"] else QFont.Weight.Normal
            )
        if "italic" in changes:
            fmt.setFontItalic(bool(changes["italic"]))
        if "underline" in changes:
            fmt.setFontUnderline(bool(changes["underline"]))
        if changes.get("toggleBold"):
            cur = cursor.charFormat().fontWeight()
            fmt.setFontWeight(
                QFont.Weight.Normal
                if cur >= int(QFont.Weight.Bold)
                else QFont.Weight.Bold
            )
        if changes.get("toggleItalic"):
            fmt.setFontItalic(not cursor.charFormat().fontItalic())
        if changes.get("toggleUnderline"):
            fmt.setFontUnderline(not cursor.charFormat().fontUnderline())
        if "lineSpacing" in changes:
            pt = max(6.0, float(changes["lineSpacing"]))
            style = dict(self._box.get("style") or {})
            style["lineSpacing"] = pt
            self._box["style"] = resolve_text_style(style)
            self._apply_block_line_spacing(pt)
        if cursor.hasSelection():
            # 現仕様維持: 選択範囲がある時は選択文字だけに適用。
            cursor.mergeCharFormat(fmt)
            self._editor.setTextCursor(cursor)
        else:
            # 新仕様: 未選択時は編集中テキストボックス内の全文字へ適用。
            whole_cursor = QTextCursor(self._editor.document())
            whole_cursor.select(QTextCursor.SelectionType.Document)
            if whole_cursor.hasSelection():
                whole_cursor.mergeCharFormat(fmt)
            # 以後の入力にも同じ書式を継続適用。
            self._editor.mergeCurrentCharFormat(fmt)
        self._sync_editor_to_box()
        self._update_display_content()
        self._emit_char_format_state()
        self.changed.emit()
        QTimer.singleShot(0, self._focus_editor)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        if not self._selected and self._editing:
            self.finish_editing()
        self._apply_style()

    def set_display_scale(self, scale: float) -> None:
        self._scale = max(0.01, float(scale))
        self._apply_style()
        self._apply_geometry()

    def start_editing(self, *, caret_at_end: bool = False, record_undo: bool = True) -> None:
        if not self._selected:
            self.selected.emit(self.box_id)
        if record_undo and not self._editing:
            self.editing_started.emit()
        self._set_editing_mode(True)
        if caret_at_end:
            QTimer.singleShot(0, self._focus_editor_at_end)
        else:
            QTimer.singleShot(0, self._focus_editor)
        QTimer.singleShot(0, self._emit_char_format_state)

    def focus_caret_at_end(self) -> bool:
        """編集中のテキスト末尾へカーソルを移動してフォーカスする。"""
        if not self._editing:
            self._set_editing_mode(True)
        return self._focus_editor_at_end()

    def prepare_speech_input(self) -> bool:
        """Windows 音声入力の直前に、末尾カーソルとエディタフォーカスを確実にする。"""
        from ui_qt.speech.windows_voice_typing import focus_widget_for_voice_input

        if not self._selected:
            self.selected.emit(self.box_id)
        self._suppress_focus_check = True
        self._set_editing_mode(True)
        self.raise_()
        self._editor.raise_()
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)
        self._editor.ensureCursorVisible()
        focus_widget_for_voice_input(self._editor)
        return self.is_editor_focused_at_end()

    def is_editor_focused_at_end(self) -> bool:
        if not self._editing:
            return False
        if not self._editor.hasFocus():
            return False
        text = self._editor.toPlainText()
        return self._editor.textCursor().position() >= len(text)

    def release_speech_input_guard(self) -> None:
        self._suppress_focus_check = False

    def _focus_editor_at_end(self) -> bool:
        if not self._editing:
            return False
        from ui_qt.speech.windows_voice_typing import focus_widget_for_voice_input

        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)
        self._editor.ensureCursorVisible()
        focus_widget_for_voice_input(self._editor)
        return self.is_editor_focused_at_end()

    def _focus_editor(self) -> None:
        if not self._editing:
            return
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def finish_editing(self) -> None:
        if not self._editing:
            return
        self._suppress_focus_check = True
        self._editor.clearFocus()
        self._press_origin = None
        self._moving = False
        self._set_editing_mode(False)
        self._suppress_focus_check = False
        self.editing_committed.emit()
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

    def set_preview_mode(self, enabled: bool) -> None:
        self._preview_mode = bool(enabled)
        if self._preview_mode:
            self._moving = False
            self._resizing = False
            self._press_origin = None
        self._update_handles()
        if self._preview_mode:
            self.unsetCursor()

    def set_preview_resize_enabled(self, enabled: bool) -> None:
        self._preview_resize = bool(enabled)
        self._update_handles()

    def set_focus_guard_widgets(self, widgets: list[QWidget] | tuple[QWidget, ...]) -> None:
        self._focus_guard_widgets = [w for w in widgets if w is not None]

    def _has_rich_html(self) -> bool:
        return (
            str(self._box.get("textFormat") or "") == TEXT_FORMAT_HTML
            and bool(str(self._box.get("textHtml") or "").strip())
        )

    def append_transcript(self, text: str) -> None:
        chunk = str(text or "").strip()
        if not chunk:
            return
        if not self._editing:
            self.start_editing(record_undo=False)
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self._editor.setTextCursor(cursor)
        self._sync_editor_to_box()
        self._update_display_content()
        self.changed.emit()

    def apply_style_dict(self, style: dict[str, Any]) -> None:
        merged = {**dict(self._box.get("style") or {}), **style}
        self._box["style"] = resolve_text_style(merged)
        self._apply_style()
        if self._editing:
            self._apply_default_char_format()
            self._apply_block_line_spacing()
        elif self._has_rich_html():
            self._load_editor_content()
            self._apply_block_line_spacing()
        else:
            sync_box_html_from_style(self._box)
            self._load_editor_content()
            self._apply_block_line_spacing()

    def _set_editing_mode(self, editing: bool) -> None:
        self._editing = bool(editing)
        self._text_stack.setAttribute(Qt.WA_TransparentForMouseEvents, not editing)
        if editing:
            self._text_stack.setCurrentWidget(self._editor)
            self._setup_tight_document()
            self._apply_default_char_format()
            self.unsetCursor()
        else:
            self._sync_editor_to_box()
            self._update_display_content()
            self._text_stack.setCurrentWidget(self._display_label)
        self._apply_style()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor and event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._check_editing_finished)
        return super().eventFilter(watched, event)

    def _check_editing_finished(self) -> None:
        if self._suppress_focus_check or not self._editing:
            return
        if self._is_editing_focus_retained():
            return
        self._set_editing_mode(False)
        self.editing_committed.emit()
        self.changed.emit()
        self.editing_finished.emit(self.box_id)

    def _is_editing_focus_retained(self) -> bool:
        fw = QApplication.focusWidget()
        w: QWidget | None = fw
        while w is not None:
            if w is self._editor:
                return True
            for guard in self._focus_guard_widgets:
                if w is guard or guard.isAncestorOf(w):
                    return True
            w = w.parentWidget()
        return False

    def _style(self) -> dict[str, Any]:
        st = self._box.get("style") or {}
        merged = dict(TEXT_STYLE_TEMPLATE_A)
        if isinstance(st, dict):
            merged.update(st)
        return resolve_text_style(merged)

    def _body_display_size(self) -> tuple[int, int]:
        w = max(16, int(float(self._box.get("width") or _MIN_NATIVE_W) / self._scale))
        h = max(16, int(float(self._box.get("height") or _MIN_NATIVE_H) / self._scale))
        return w, h

    def _apply_geometry(self) -> None:
        bw, bh = self._body_display_size()
        total_w = bw + _HANDLE_OVERHANG * 2
        total_h = bh + _HANDLE_OVERHANG * 2
        if self._preview_mode:
            self.resize(total_w, total_h)
            self._body.setGeometry(_HANDLE_OVERHANG, _HANDLE_OVERHANG, bw, bh)
            self._body.setMinimumSize(bw, bh)
            self._update_handles()
            return
        x = int(float(self._box.get("x") or 0) / self._scale) - _HANDLE_OVERHANG
        y = int(float(self._box.get("y") or 0) / self._scale) - _HANDLE_OVERHANG
        self.setGeometry(
            x,
            y,
            total_w,
            total_h,
        )
        self._body.setGeometry(_HANDLE_OVERHANG, _HANDLE_OVERHANG, bw, bh)
        self._body.setMinimumSize(bw, bh)
        self._update_handles()

    def _update_handles(self) -> None:
        show = self._selected and not self._editing and (
            not self._preview_mode or self._preview_resize
        )
        half = _HANDLE_SIZE // 2
        ox = _HANDLE_OVERHANG
        oy = _HANDLE_OVERHANG
        bw, bh = self._body_display_size()
        positions = {
            "tl": (ox - half, oy - half),
            "tr": (ox + bw - half, oy - half),
            "bl": (ox - half, oy + bh - half),
            "br": (ox + bw - half, oy + bh - half),
        }
        for corner, handle in self._handles.items():
            if show:
                px, py = positions[corner]
                handle.setGeometry(px, py, _HANDLE_SIZE, _HANDLE_SIZE)
                handle.show()
                handle.raise_()
            else:
                handle.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        bw = max(16, self.width() - _HANDLE_OVERHANG * 2)
        bh = max(16, self.height() - _HANDLE_OVERHANG * 2)
        self._body.setGeometry(_HANDLE_OVERHANG, _HANDLE_OVERHANG, bw, bh)
        self._update_handles()
        self._apply_vertical_text_inset()

    def _content_font(self) -> QFont:
        st = self._style()
        font = QFont()
        base_pt = float(st.get("fontSize") or _DEFAULT_FONT_PT)
        disp_pt = max(6, int(round(base_pt / self._scale)))
        font.setPointSize(disp_pt)
        return font

    def _load_editor_content(self) -> None:
        html = box_text_html(self._box, self._style())
        self._editor.blockSignals(True)
        try:
            self._editor.setHtml(html)
            self._setup_tight_document()
        finally:
            self._editor.blockSignals(False)
        self._sync_editor_to_box()
        self._update_display_content()

    def _sync_editor_to_box(self) -> None:
        plain = self._editor.toPlainText()
        html = self._editor.toHtml()
        mark_box_html(self._box, html, plain)
        first_char_color = self._first_char_color_hex()
        if first_char_color:
            style = dict(self._box.get("style") or {})
            if str(style.get("textColor") or "").lower() != first_char_color.lower():
                style["textColor"] = first_char_color
                self._box["style"] = resolve_text_style(style)

    def _first_char_color_hex(self) -> str | None:
        # 空文や短い文で setPosition を触れないよう、fragment から先頭色を取る。
        if not self._editor.toPlainText():
            return None
        block = self._editor.document().firstBlock()
        if not block.isValid():
            return None
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.length() > 0:
                color = frag.charFormat().foreground().color()
                if color.isValid():
                    return color.name(QColor.NameFormat.HexRgb)
            it += 1
        color = block.charFormat().foreground().color()
        if not color.isValid():
            return None
        return color.name(QColor.NameFormat.HexRgb)

    def _update_display_content(self) -> None:
        html = box_text_html(self._box, self._style())
        self._display_label.setText(html_body_for_label(html) or self._editor.toPlainText())

    def _apply_default_char_format(self) -> None:
        st = self._style()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(str(st.get("textColor") or DEFAULT_TEXT_COLOR)))
        fmt.setFontPointSize(max(6.0, float(st.get("fontSize") or _DEFAULT_FONT_PT)))
        fmt.setFontFamily("Meiryo")
        self._editor.setCurrentCharFormat(fmt)

    def _emit_char_format_state(self) -> None:
        if self._syncing_format_ui or not self._editing:
            return
        self.char_format_state_changed.emit(self.current_char_format_state())

    def _apply_style(self) -> None:
        st = self._style()
        fa = float(st.get("fillAlpha", 0))
        fill = st.get("fillColor") or "#ffffff"
        tc = st.get("textColor") or DEFAULT_TEXT_COLOR

        bg = "transparent" if fa <= 0 else f"rgba({_hex_rgb(fill)}, {fa})"
        chrome = self._text_tool_mode or self._selected or self._editing
        if chrome:
            if self._editing:
                bg = "rgba(255, 255, 255, 0.92)" if fa <= 0 else bg
                border_css = "2px solid #2563eb"
            elif self._selected:
                border_css = "1px solid #2563eb"
                if fa <= 0:
                    bg = "rgba(255, 255, 255, 0.35)"
            else:
                border_css = "1px dashed rgba(37, 99, 235, 0.55)"
                if fa <= 0:
                    bg = "rgba(255, 255, 255, 0.2)"
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
        if self._editing:
            pal.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255, 240))
        else:
            pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self._editor.setPalette(pal)
        editor_bg = "rgba(255, 255, 255, 0.92)" if self._editing else "transparent"
        css = (
            f"QTextEdit#TextBoxEditor {{ background: {editor_bg}; "
            f"border: none; padding: 0px; margin: 0px; }}"
        )
        self._editor.setStyleSheet(css)
        self._editor.viewport().setStyleSheet(
            f"background: {editor_bg}; border: none;"
        )
        label_css = (
            "QLabel#TextBoxDisplayLabel { background: transparent; "
            "border: none; padding: 0px; margin: 0px; }"
        )
        self._display_label.setStyleSheet(label_css)
        self._apply_text_alignment()
        if not self._editing:
            self._update_display_content()
        self._update_handles()

    def _apply_text_alignment(self) -> None:
        st = self._style()
        qt_align = qt_label_alignment(st)
        self._display_label.setAlignment(qt_align)
        h_align = qt_horizontal_alignment(st)
        option = self._editor.document().defaultTextOption()
        option.setAlignment(h_align)
        self._editor.document().setDefaultTextOption(option)
        doc = self._editor.document()
        block = doc.firstBlock()
        while block.isValid():
            cursor = QTextCursor(block)
            block_fmt = cursor.blockFormat()
            block_fmt.setAlignment(h_align)
            cursor.mergeBlockFormat(block_fmt)
            block = block.next()
        self._apply_vertical_text_inset()

    def _apply_vertical_text_inset(self) -> None:
        bw = max(1, self._body.width())
        self._editor.document().setTextWidth(max(1.0, float(bw)))
        _, v = normalize_text_align(self._style())
        content_h = self._editor.document().size().height()
        body_h = max(1.0, float(self._body.height()))
        free = max(0.0, body_h - content_h)
        if v == "center":
            top = int(free / 2)
        elif v == "bottom":
            top = int(free)
        else:
            top = 0
        self._editor.setViewportMargins(0, top, 0, 0)

    def _line_spacing_pt(self) -> float:
        st = self._style()
        return max(
            6.0,
            float(
                st.get("lineSpacing")
                or _DEFAULT_LINE_SPACING_PT
                or st.get("fontSize")
                or _DEFAULT_FONT_PT
            ),
        )

    def _apply_block_line_spacing(self, pt: float | None = None) -> None:
        line_pt = max(6.0, float(pt if pt is not None else self._line_spacing_pt()))
        block_fmt = QTextBlockFormat()
        block_fmt.setTopMargin(0)
        block_fmt.setBottomMargin(0)
        block_fmt.setLineHeight(
            line_pt, int(QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        )
        doc = self._editor.document()
        self._editor.blockSignals(True)
        try:
            block = doc.firstBlock()
            while block.isValid():
                cursor = QTextCursor(block)
                cursor.mergeBlockFormat(block_fmt)
                block = block.next()
        finally:
            self._editor.blockSignals(False)
        self._apply_text_alignment()
        self._sync_editor_to_box()
        if not self._editing:
            self._update_display_content()

    def _setup_tight_document(self) -> None:
        doc = self._editor.document()
        doc.setDocumentMargin(0)
        self._apply_block_line_spacing()

    def _on_text_changed(self) -> None:
        self._sync_editor_to_box()
        self._update_display_content()
        self._apply_vertical_text_inset()
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
            self.interactive_change_started.emit()
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
            self.interactive_change_finished.emit()
            self.changed.emit()
        self._press_origin = None
        self._press_moved = False
        self._moving = False

    def _begin_resize(self, corner: str, global_pos: QPoint) -> None:
        if self._preview_mode and not self._preview_resize:
            return
        if not self._selected:
            self.selected.emit(self.box_id)
        self._press_origin = None
        self._moving = False
        self._resizing = True
        self._resize_corner = corner
        self._resize_origin = global_pos
        ox = float(self._box.get("x") or 0)
        oy = float(self._box.get("y") or 0)
        ow = float(self._box.get("width") or _MIN_NATIVE_W)
        oh = float(self._box.get("height") or _MIN_NATIVE_H)
        self._resize_orig_box = (ox, oy, ow, oh)
        self.interactive_change_started.emit()
        self.grabMouse()

    def _update_resize(self, global_pos: QPoint) -> None:
        if not self._resizing or self._resize_corner is None or self._resize_orig_box is None:
            return
        delta = global_pos - self._resize_origin
        ds = self._scale
        dx = delta.x() * ds
        dy = delta.y() * ds
        ox, oy, ow, oh = self._resize_orig_box
        x, y, w, h = ox, oy, ow, oh
        corner = self._resize_corner

        if self._preview_mode:
            if corner in ("br", "tr"):
                w = max(_MIN_NATIVE_W, ow + dx)
            if corner in ("bl", "tl"):
                w = max(_MIN_NATIVE_W, ow - dx)
            if corner in ("br", "bl"):
                h = max(_MIN_NATIVE_H, oh + dy)
            if corner in ("tr", "tl"):
                h = max(_MIN_NATIVE_H, oh - dy)
            x, y = 0.0, 0.0
        else:
            if corner in ("br", "tr"):
                w = max(_MIN_NATIVE_W, ow + dx)
            if corner in ("bl", "tl"):
                w = max(_MIN_NATIVE_W, ow - dx)
                x = ox + ow - w
            if corner in ("br", "bl"):
                h = max(_MIN_NATIVE_H, oh + dy)
            if corner in ("tr", "tl"):
                h = max(_MIN_NATIVE_H, oh - dy)
                y = oy + oh - h

        self._box["x"] = x
        self._box["y"] = y
        self._box["width"] = w
        self._box["height"] = h
        self._apply_geometry()
        if self._preview_mode:
            self.changed.emit()

    def _end_resize(self) -> None:
        if self._resizing:
            self.interactive_change_finished.emit()
            self.changed.emit()
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._resizing = False
        self._resize_corner = None
        self._resize_orig_box = None

    def _point_in_body(self, pos: QPoint) -> bool:
        return self._body.geometry().contains(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and not self._editing
            and not self._preview_mode
        ):
            if not self._point_in_body(event.position().toPoint()):
                super().mousePressEvent(event)
                return
            self._begin_pointer(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._resizing:
            self._update_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if not self._preview_mode and event.buttons() & Qt.LeftButton:
            self._update_move_drag(event.globalPosition().toPoint())
            if self._moving:
                event.accept()
                return
        if (
            not self._preview_mode
            and not self._editing
            and self._point_in_body(event.position().toPoint())
        ):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self._resizing:
                self._end_resize()
            else:
                self._end_pointer()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and not self._editing
            and self._point_in_body(event.position().toPoint())
        ):
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
