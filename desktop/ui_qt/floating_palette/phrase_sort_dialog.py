"""定型文の並び替えルール設定ダイアログ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QDropEvent
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


class _ReorderListWidget(QListWidget):
    """InternalMove で「上に重ねると消える」を避け、必ず上下へ挿入する。"""

    orderChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.source() is not self:
            event.ignore()
            return
        source_item = self.currentItem()
        if source_item is None:
            event.ignore()
            return
        source_row = self.row(source_item)
        if source_row < 0:
            event.ignore()
            return

        pos = event.position().toPoint()
        target_item = self.itemAt(pos)
        if target_item is None:
            dest_row = self.count()
        else:
            target_row = self.row(target_item)
            rect = self.visualItemRect(target_item)
            # 上半分 → その行の前、下半分 → その行の後
            if pos.y() < rect.center().y():
                dest_row = target_row
            else:
                dest_row = target_row + 1

        # takeItem 後のインデックス補正
        if source_row < dest_row:
            dest_row -= 1
        dest_row = max(0, min(dest_row, self.count() - 1))

        if dest_row == source_row:
            event.accept()
            return

        item = self.takeItem(source_row)
        if item is None:
            event.ignore()
            return
        self.insertItem(dest_row, item)
        self.setCurrentItem(item)
        event.accept()
        self.orderChanged.emit()


class PhraseSortDialog(QDialog):
    """定型文の並び替えルール設定。ドラッグで順序変更するとユーザー指定へ切替。"""

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
            "どのルールでも ⋮⋮ をドラッグして並べ替えでき、"
            "動かした瞬間に「ユーザー指定」へ切り替わります。"
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

        self._drag_hint = QLabel("⋮⋮ をつかんで上下にドラッグして並べ替え")
        self._drag_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(self._drag_hint)

        self._list = _ReorderListWidget()
        self._list.setObjectName("PhraseSortList")
        self._list.setSpacing(4)
        self._list.orderChanged.connect(self._on_list_reordered)
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

        self._rebuild_list()

    def _current_mode(self) -> str:
        return str(self._mode.currentData() or PHRASE_SORT_CUSTOM)

    def _on_mode_changed(self, _index: int = 0) -> None:
        self._rebuild_list()

    def _switch_combo_to_custom(self) -> None:
        if self._current_mode() == PHRASE_SORT_CUSTOM:
            return
        self._mode.blockSignals(True)
        for i in range(self._mode.count()):
            if self._mode.itemData(i) == PHRASE_SORT_CUSTOM:
                self._mode.setCurrentIndex(i)
                break
        self._mode.blockSignals(False)

    def _on_list_reordered(self) -> None:
        # 現在の並びを基準にユーザー指定へ（一覧は再構築しない）
        self._switch_combo_to_custom()

    def _ordered_templates(self) -> list[dict[str, Any]]:
        mode = self._current_mode()
        if mode == PHRASE_SORT_CUSTOM:
            # リスト上の現在順があればそれを優先（ドラッグ直後など）
            live_ids = self._ids_from_list()
            if live_ids and self._list.count() == len(self._templates):
                ordered: list[dict[str, Any]] = []
                seen: set[str] = set()
                for pid in live_ids:
                    tpl = self._by_id.get(pid)
                    if tpl is not None and pid not in seen:
                        ordered.append(tpl)
                        seen.add(pid)
                for tpl in self._templates:
                    pid = str(tpl.get("id") or "")
                    if pid and pid not in seen:
                        ordered.append(tpl)
                        seen.add(pid)
                if ordered:
                    return ordered
        return sort_phrase_templates(self._templates, mode=mode)

    def _rebuild_list(self) -> None:
        self._list.clear()
        for tpl in self._ordered_templates():
            self._list.addItem(self._make_item(tpl))

    def _make_item(self, tpl: dict[str, Any]) -> QListWidgetItem:
        pid = str(tpl.get("id") or "")
        label = phrase_simple_button_label(tpl)
        item = QListWidgetItem(f"⋮⋮  {label}")
        item.setData(Qt.UserRole, pid)
        item.setToolTip(phrase_preview_text(tpl) or label)
        # ItemIsDropEnabled を付けない（上に落とすと置換・消滅しやすいため）
        item.setFlags(
            Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
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
            item = self._list.item(i)
            if item is None:
                continue
            pid = str(item.data(Qt.UserRole) or "").strip()
            if pid:
                out.append(pid)
        return out

    def _on_apply(self) -> None:
        mode = self._current_mode()
        save_phrase_sort_mode(mode)
        if mode == PHRASE_SORT_CUSTOM:
            save_phrase_custom_order(self._ids_from_list())
        self.accept()
