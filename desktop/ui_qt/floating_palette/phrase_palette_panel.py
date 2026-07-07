"""定型文（フレーズシール）パネル。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
    phrase_templates_mru,
)
from ui_qt.floating_palette.text_rich import box_text_html, html_body_for_label


class PhrasePalettePanel(QWidget):
    """定型文の選択・登録 UI。"""

    phrase_selected = Signal(str)
    copy_from_textbox_requested = Signal()
    placement_cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_mode = VIEW_SIMPLE
        self._pending_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._hint = QLabel(
            "定型文を選び、画像上をクリックして貼り付け\n"
            "貼り付け後もテキストボックスとして編集できます"
        )
        self._hint.setObjectName("PaletteHintLabel")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

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
        self._scroll_host = QWidget()
        self._detailed_lay = QVBoxLayout(self._scroll_host)
        self._detailed_lay.setContentsMargins(0, 0, 0, 0)
        self._detailed_lay.setSpacing(6)
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

        self._phrase_btns: dict[str, QPushButton] = {}
        self.reload_templates()

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()

    def _apply_view_mode(self) -> None:
        detailed = self._view_mode == VIEW_DETAILED
        self._simple_frame.setVisible(not detailed)
        self._scroll.setVisible(detailed)

    def reload_templates(self) -> None:
        templates = phrase_templates_mru()
        self._rebuild_buttons(templates)
        if self._pending_id and self._pending_id not in self._phrase_btns:
            self.set_pending_phrase(None)

    def set_pending_phrase(self, phrase_id: str | None) -> None:
        self._pending_id = str(phrase_id) if phrase_id else None
        for pid, btn in self._phrase_btns.items():
            btn.setChecked(pid == self._pending_id)
        if self._pending_id:
            btn = self._phrase_btns.get(self._pending_id)
            label = btn.toolTip() if btn else self._pending_id
            self._pending_label.setText(
                f"配置待ち: {label}\n貼り付ける場所をクリックしてください"
            )
            self._pending_label.show()
        else:
            self._pending_label.clear()
            self._pending_label.hide()

    def _rebuild_buttons(self, templates: list[dict[str, Any]]) -> None:
        for btn in self._phrase_btns.values():
            btn.deleteLater()
        self._phrase_btns.clear()

        while self._simple_grid.count():
            item = self._simple_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        while self._detailed_lay.count():
            item = self._detailed_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        simple_items = templates[:PHRASE_SIMPLE_COUNT]
        for i, tpl in enumerate(simple_items):
            btn = self._make_phrase_btn(tpl, compact=True)
            row, col = divmod(i, 2)
            self._simple_grid.addWidget(btn, row, col)

        for tpl in templates:
            row = QHBoxLayout()
            row.setSpacing(6)
            btn = self._make_phrase_btn(tpl, compact=False)
            row.addWidget(btn, 1)
            del_btn = QPushButton("削除")
            del_btn.setProperty("variant", "danger")
            del_btn.setFixedWidth(44)
            pid = str(tpl.get("id") or "")
            del_btn.clicked.connect(lambda _c=False, p=pid: self._on_delete(p))
            row.addWidget(del_btn)
            host = QWidget()
            host.setLayout(row)
            self._detailed_lay.addWidget(host)

        self._detailed_lay.addStretch()
        self.set_pending_phrase(self._pending_id)

    def _make_phrase_btn(self, tpl: dict[str, Any], *, compact: bool) -> QPushButton:
        pid = str(tpl.get("id") or "")
        label = str(tpl.get("label") or tpl.get("text") or "定型文")
        display = label
        if compact and len(display) > 8:
            display = display[:7] + "…"
        btn = QPushButton(display)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        preview = self._preview_text(tpl)
        btn.setToolTip(preview or label)
        btn.clicked.connect(lambda _c=False, p=pid: self._on_phrase_clicked(p))
        self._phrase_btns[pid] = btn
        return btn

    def _preview_text(self, tpl: dict[str, Any]) -> str:
        html = box_text_html(
            {
                "text": tpl.get("text"),
                "textHtml": tpl.get("textHtml"),
                "textFormat": tpl.get("textFormat"),
                "style": tpl.get("style"),
            },
            tpl.get("style"),
        )
        plain = str(tpl.get("text") or "").strip()
        body = html_body_for_label(html)
        return plain or body.replace("<br>", "\n").replace("<br/>", "\n")

    def _on_phrase_clicked(self, phrase_id: str) -> None:
        if self._pending_id == phrase_id:
            self.set_pending_phrase(None)
            self.placement_cancel_requested.emit()
            return
        self.set_pending_phrase(phrase_id)
        self.phrase_selected.emit(phrase_id)

    def _on_delete(self, phrase_id: str) -> None:
        delete_phrase_template(phrase_id)
        self.reload_templates()
