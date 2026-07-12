"""定型文の並び替えルール設定ダイアログ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt.floating_palette.phrase_template_prefs import (
    PHRASE_SORT_CUSTOM,
    PHRASE_SORT_MODES,
    load_phrase_sort_mode,
    phrase_preview_text,
    phrase_simple_button_label,
    save_phrase_custom_order,
    save_phrase_sort_mode,
    sort_phrase_templates,
)
from ui_qt.floating_palette.text_rich import palette_fill_background
from ui_qt.style import COLORS


class PhraseSortDialog(QDialog):
    """定型文の並び替えルールを選び、ユーザー指定時はドラッグで順序編集する。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        templates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("定型文並び替え")
        self.resize(420, 520)
        self.setModal(True)
        self._templates = list(templates or [])
        self._by_id = {str(t.get("id") or ""): t for t in self._templates}

        root = QVBoxLayout(self)
        root.setSpacing(8)

        hint = QLabel(
            "並び替えルールを選ぶと一覧の表示順が変わります。"
            "「ユーザー指定」ではグラバーをドラッグして順序を変えられます。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(hint)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("順序"))
        self._mode = QComboBox()
        for key, label in PHRASE_SORT_MODES:
            self._mode.addItem(label, key)
        current = load_phrase_sort_mode()
        for i in range(self._mode.count()):
            if self._mode.itemData(i) == current:
                self._mode.setCurrentIndex(i)
                break
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode, 1)
        root.addLayout(mode_row)

        self._drag_hint = QLabel("")
        self._drag_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(self._drag_hint)

        self._list = QListWidget()
        self._list.setObjectName("PhraseSortList")
        self._list.setSpacing(4)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setStyleSheet(
            f"""
            QListWidget#PhraseSortList {{
                background: {COLORS['sidebar']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                padding: 6px;
            }}
            QListWidget#PhraseSortList::item {{
                margin: 2px 0;
                border-radius: 6px;
                padding: 0;
            }}
            """
        )
        root.addWidget(self._list, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("適用")
        ok.setObjectName("PrimaryBtn")
        ok.clicked.connect(self._on_apply)
        btns.addWidget(ok)
        root.addLayout(btns)

        self._rebuild_list(preserve_custom_ids=None)
        self._update_drag_mode()

    def _current_mode(self) -> str:
        return str(self._mode.currentData() or PHRASE_SORT_CUSTOM)

    def _on_mode_changed(self, _index: int = 0) -> None:
        self._rebuild_list(preserve_custom_ids=None)
        self._update_drag_mode()

    def _update_drag_mode(self) -> None:
        custom = self._current_mode() == PHRASE_SORT_CUSTOM
        self._list.setDragEnabled(custom)
        self._list.setAcceptDrops(custom)
        self._list.setDragDropMode(
            QAbstractItemView.InternalMove if custom else QAbstractItemView.NoDragDrop
        )
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._drag_hint.setText(
            "⋮⋮ をつかんで上下にドラッグして並べ替え" if custom else ""
        )

    def _ordered_templates(
        self, *, preserve_custom_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        mode = self._current_mode()
        if mode == PHRASE_SORT_CUSTOM and preserve_custom_ids is not None:
            ordered: list[dict[str, Any]] = []
            seen: set[str] = set()
            for pid in preserve_custom_ids:
                tpl = self._by_id.get(pid)
                if tpl is not None and pid not in seen:
                    ordered.append(tpl)
                    seen.add(pid)
            for tpl in self._templates:
                pid = str(tpl.get("id") or "")
                if pid and pid not in seen:
                    ordered.append(tpl)
                    seen.add(pid)
            return ordered
        return sort_phrase_templates(self._templates, mode=mode)

    def _rebuild_list(self, *, preserve_custom_ids: list[str] | None) -> None:
        self._list.clear()
        for tpl in self._ordered_templates(preserve_custom_ids=preserve_custom_ids):
            self._list.addItem(self._make_item(tpl))

    def _make_item(self, tpl: dict[str, Any]) -> QListWidgetItem:
        pid = str(tpl.get("id") or "")
        label = phrase_simple_button_label(tpl)
        item = QListWidgetItem(f"⋮⋮  {label}")
        item.setData(Qt.UserRole, pid)
        item.setToolTip(phrase_preview_text(tpl) or label)
        item.setFlags(
            Qt.ItemIsEnabled
            | Qt.ItemIsSelectable
            | Qt.ItemIsDragEnabled
            | Qt.ItemIsDropEnabled
        )
        item.setSizeHint(QSize(200, 36))
        bg = palette_fill_background(tpl.get("style") or {})
        item.setBackground(self._qcolor(bg))
        font = QFont(item.font())
        font.setPixelSize(12)
        item.setFont(font)
        return item

    @staticmethod
    def _qcolor(css_color: str) -> QColor:
        c = QColor(str(css_color or "").strip() or COLORS["surface"])
        if not c.isValid():
            c = QColor(COLORS["surface"])
        return c

    def _ids_from_list(self) -> list[str]:
        out: list[str] = []
        for i in range(self._list.count()):
            pid = str(self._list.item(i).data(Qt.UserRole) or "").strip()
            if pid:
                out.append(pid)
        return out

    def _on_apply(self) -> None:
        mode = self._current_mode()
        save_phrase_sort_mode(mode)
        if mode == PHRASE_SORT_CUSTOM:
            save_phrase_custom_order(self._ids_from_list())
        self.accept()
