"""定型文（フレーズシール）パネル。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_qt.floating_palette.palette_prefs import VIEW_DETAILED, VIEW_SIMPLE
from ui_qt.floating_palette.phrase_template_prefs import (
    PHRASE_SIMPLE_COUNT,
    delete_phrase_template,
    phrase_display_label,
    phrase_has_content,
    phrase_preview_text,
    phrase_templates_mru,
)
from ui_qt.style import COLORS


class PhrasePalettePanel(QWidget):
    """定型文の選択・登録 UI。"""

    phrase_selected = Signal(str)
    phrase_edit_requested = Signal(str)
    phrase_deleted = Signal(str)
    copy_from_textbox_requested = Signal()
    placement_cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_mode = VIEW_SIMPLE
        self._pending_id: str | None = None
        self._editing_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._pending_label = QLabel("")
        self._pending_label.setObjectName("PaletteHintLabel")
        self._pending_label.setWordWrap(True)
        self._pending_label.hide()
        root.addWidget(self._pending_label)

        self._simple_frame = QFrame()
        self._simple_grid = QGridLayout(self._simple_frame)
        self._simple_grid.setContentsMargins(0, 0, 0, 0)
        self._simple_grid.setSpacing(6)
        root.addWidget(self._simple_frame)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll_host = QWidget()
        self._scroll_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._detailed_lay = QVBoxLayout(self._scroll_host)
        self._detailed_lay.setContentsMargins(0, 0, 0, 0)
        self._detailed_lay.setSpacing(8)
        self._detailed_lay.addStretch()
        self._scroll.setWidget(self._scroll_host)
        root.addWidget(self._scroll, 1)
        self._scroll.hide()

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._copy_btn = QPushButton("テキストボックスからコピー")
        self._copy_btn.setToolTip(
            "選択中のテキストボックスを書式込みで定型文として登録"
        )
        self._copy_btn.clicked.connect(self.copy_from_textbox_requested.emit)
        action_row.addWidget(self._copy_btn, 1)
        root.addLayout(action_row)

        self._select_group = QButtonGroup(self)
        self._select_group.setExclusive(True)
        self._phrase_btns: dict[str, QPushButton] = {}
        self._detail_rows: dict[str, QFrame] = {}
        self.reload_templates()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, 200)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(260, 280)

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()
        self.reload_templates()

    def _apply_view_mode(self) -> None:
        detailed = self._view_mode == VIEW_DETAILED
        self._simple_frame.setVisible(not detailed)
        self._scroll.setVisible(detailed)

    def reload_templates(self) -> None:
        templates = phrase_templates_mru()
        self._rebuild_buttons(templates)
        if self._pending_id and self._pending_id not in self._phrase_btns:
            self.set_pending_phrase(None)
        if self._editing_id and self._editing_id not in self._phrase_btns:
            self.set_editing_phrase(None)

    def set_pending_phrase(self, phrase_id: str | None) -> None:
        self._pending_id = str(phrase_id) if phrase_id else None
        for pid, btn in self._phrase_btns.items():
            btn.blockSignals(True)
            btn.setChecked(pid == self._pending_id)
            btn.blockSignals(False)
        if self._pending_id:
            tpl_label = phrase_display_label(
                self._template_for_id(self._pending_id) or {}, compact=False
            )
            self._pending_label.setText(
                f"配置待ち: {tpl_label}\nドラッグして貼り付ける位置を指定してください"
            )
            self._pending_label.show()
        else:
            self._pending_label.clear()
            self._pending_label.hide()

    def set_editing_phrase(self, phrase_id: str | None) -> None:
        self._editing_id = str(phrase_id) if phrase_id else None
        for pid, frame in self._detail_rows.items():
            frame.setProperty("editing", pid == self._editing_id)
            frame.style().unpolish(frame)
            frame.style().polish(frame)

    def _template_for_id(self, phrase_id: str) -> dict[str, Any] | None:
        for tpl in phrase_templates_mru():
            if str(tpl.get("id")) == phrase_id:
                return tpl
        return None

    def _clear_layout_widgets(self, layout: QGridLayout | QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_buttons(self, templates: list[dict[str, Any]]) -> None:
        for btn in self._phrase_btns.values():
            self._select_group.removeButton(btn)
            btn.deleteLater()
        self._phrase_btns.clear()
        self._detail_rows.clear()

        if self._view_mode == VIEW_SIMPLE:
            self._clear_layout_widgets(self._simple_grid)
            self._scroll_host.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
            )
            simple_items = templates[:PHRASE_SIMPLE_COUNT]
            for i, tpl in enumerate(simple_items):
                btn = self._make_select_btn(tpl, compact=True)
                row, col = divmod(i, 2)
                self._simple_grid.addWidget(btn, row, col)
        else:
            self._clear_layout_widgets(self._detailed_lay)
            self._detailed_lay.addStretch()
            self._scroll_host.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
            for tpl in templates:
                frame = self._make_detailed_row(tpl)
                self._detailed_lay.insertWidget(self._detailed_lay.count() - 1, frame)

        self.set_pending_phrase(self._pending_id)
        self.set_editing_phrase(self._editing_id)

    def _style_select_btn(self, btn: QPushButton, tpl: dict[str, Any]) -> None:
        if not phrase_has_content(tpl):
            btn.setStyleSheet(f"color: {COLORS['danger']};")
        else:
            btn.setStyleSheet("")

    def _make_select_btn(self, tpl: dict[str, Any], *, compact: bool) -> QPushButton:
        pid = str(tpl.get("id") or "")
        display = phrase_display_label(tpl, compact=compact)
        btn = QPushButton(display)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        preview = phrase_preview_text(tpl)
        btn.setToolTip(preview or display)
        self._style_select_btn(btn, tpl)
        btn.clicked.connect(lambda _c=False, p=pid: self._on_phrase_clicked(p))
        self._select_group.addButton(btn)
        self._phrase_btns[pid] = btn
        return btn

    def _make_detailed_row(self, tpl: dict[str, Any]) -> QFrame:
        pid = str(tpl.get("id") or "")
        frame = QFrame()
        frame.setObjectName("PhraseDetailRow")
        frame.setProperty("editing", False)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        select_btn = self._make_select_btn(tpl, compact=False)
        select_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        lay.addWidget(select_btn)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel(phrase_display_label(tpl, compact=False))
        title.setObjectName("PhraseDetailTitle")
        if not phrase_has_content(tpl):
            title.setStyleSheet(f"color: {COLORS['danger']}; font-weight: 600;")
        preview = phrase_preview_text(tpl)
        body = QLabel(preview if preview else "（文言未登録）")
        body.setObjectName("PaletteHintLabel")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        if not preview:
            body.setStyleSheet(f"color: {COLORS['text_muted']};")
        text_col.addWidget(title)
        text_col.addWidget(body)
        lay.addLayout(text_col, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        edit_btn = QPushButton("編集")
        edit_btn.setToolTip("書式・文言をテキストボックスと同様に編集")
        edit_btn.clicked.connect(lambda _c=False, p=pid: self.phrase_edit_requested.emit(p))
        del_btn = QPushButton("削除")
        del_btn.setProperty("variant", "danger")
        del_btn.setFixedWidth(52)
        del_btn.clicked.connect(lambda _c=False, p=pid: self._on_delete(p))
        btn_col.addWidget(edit_btn)
        btn_col.addWidget(del_btn)
        btn_col.addStretch()
        lay.addLayout(btn_col)

        self._detail_rows[pid] = frame
        return frame

    def _on_phrase_clicked(self, phrase_id: str) -> None:
        if self._pending_id == phrase_id:
            self._select_group.setExclusive(False)
            btn = self._phrase_btns.get(phrase_id)
            if btn is not None:
                btn.setChecked(False)
            self._select_group.setExclusive(True)
            self.set_pending_phrase(None)
            self.placement_cancel_requested.emit()
            return
        self.set_pending_phrase(phrase_id)
        self.phrase_selected.emit(phrase_id)

    def _on_delete(self, phrase_id: str) -> None:
        if self._pending_id == phrase_id:
            self.set_pending_phrase(None)
            self.placement_cancel_requested.emit()
        if self._editing_id == phrase_id:
            self.set_editing_phrase(None)
        delete_phrase_template(phrase_id)
        self.phrase_deleted.emit(phrase_id)
        self.reload_templates()
