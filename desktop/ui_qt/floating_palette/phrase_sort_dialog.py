"""定型文の並び替えルール設定ダイアログ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QPainter,
    QPen,
)
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
    """並べ替え専用。挿入位置を線で示し、ID 順の入れ替えだけ通知する。"""

    idsReordered = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_row = -1
        self._insert_at: int | None = None  # 0..count（ドロップ前の挿入インデックス）
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # 自前の挿入線を使う
        # MoveAction だと drop 後にソース行がもう一度消されることがある
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        self._drag_row = self.currentRow()
        self._insert_at = None
        super().startDrag(supported_actions)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.source() is self:
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self._update_insert_marker(event.position().toPoint())
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.source() is self:
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self._update_insert_marker(event.position().toPoint())
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._clear_insert_marker()
        super().dragLeaveEvent(event)

    def _insert_index_at(self, pos: QPoint) -> int:
        """ドロップ前座標系での挿入位置（0 = 先頭前 … count = 末尾後）。"""
        n = self.count()
        if n <= 0:
            return 0
        target_item = self.itemAt(pos)
        if target_item is None:
            # 末尾より下、または隙間 — 最後のアイテムとの位置で判定
            last = self.item(n - 1)
            if last is None:
                return n
            last_rect = self.visualItemRect(last)
            if pos.y() < last_rect.top():
                # 先頭より上
                first = self.item(0)
                if first is not None and pos.y() < self.visualItemRect(first).center().y():
                    return 0
            return n
        target_row = self.row(target_item)
        rect = self.visualItemRect(target_item)
        if pos.y() < rect.center().y():
            return target_row
        return target_row + 1

    def _update_insert_marker(self, pos: QPoint) -> None:
        insert_at = self._insert_index_at(pos)
        if insert_at == self._insert_at:
            return
        self._insert_at = insert_at
        self.viewport().update()

    def _clear_insert_marker(self) -> None:
        if self._insert_at is None:
            return
        self._insert_at = None
        self.viewport().update()

    def _insert_line_y(self) -> int | None:
        """ビューポート座標での挿入線 Y。"""
        if self._insert_at is None:
            return None
        n = self.count()
        insert_at = self._insert_at
        margin = 2
        if n <= 0:
            return margin
        if insert_at <= 0:
            rect = self.visualItemRect(self.item(0))
            return max(1, rect.top() - 1)
        if insert_at >= n:
            rect = self.visualItemRect(self.item(n - 1))
            return rect.bottom() + 1
        # 直前アイテム下端と次アイテム上端の中間
        above = self.visualItemRect(self.item(insert_at - 1))
        below = self.visualItemRect(self.item(insert_at))
        return (above.bottom() + below.top()) // 2

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        y = self._insert_line_y()
        if y is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(COLORS["accent"])
        pen = QPen(color, 3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        left = 8
        right = self.viewport().width() - 8
        painter.drawLine(left, y, right, y)
        # 両端の小さな丸で「カーソル」感を出す
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        r = 4
        painter.drawEllipse(QPoint(left, y), r, r)
        painter.drawEllipse(QPoint(right, y), r, r)
        painter.end()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.source() is not self:
            self._clear_insert_marker()
            event.ignore()
            return

        n = self.count()
        if n <= 1:
            self._clear_insert_marker()
            event.ignore()
            return

        ids = [
            str(self.item(i).data(Qt.UserRole) or "").strip()
            for i in range(n)
            if self.item(i) is not None
        ]
        ids = [x for x in ids if x]
        if len(ids) != n:
            self._clear_insert_marker()
            event.ignore()
            return

        source_row = self._drag_row
        if source_row < 0 or source_row >= n:
            source_row = self.currentRow()
        if source_row < 0 or source_row >= n:
            self._clear_insert_marker()
            event.ignore()
            return

        pos = event.position().toPoint()
        insert_at = (
            self._insert_at if self._insert_at is not None else self._insert_index_at(pos)
        )
        insert_at = max(0, min(int(insert_at), n))

        before = list(ids)
        pid = ids.pop(source_row)
        if source_row < insert_at:
            insert_at -= 1
        insert_at = max(0, min(insert_at, len(ids)))
        ids.insert(insert_at, pid)

        self._clear_insert_marker()
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self._drag_row = -1
        if ids != before:
            self.idsReordered.emit(ids)


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

        self._drag_hint = QLabel(
            "⋮⋮ をドラッグすると、青い線が挿入位置（配置予定の場所）を示します"
        )
        self._drag_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(self._drag_hint)

        self._list = _ReorderListWidget()
        self._list.setObjectName("PhraseSortList")
        self._list.setSpacing(4)
        self._list.idsReordered.connect(self._on_ids_reordered)
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

    def _on_ids_reordered(self, ids: list[str]) -> None:
        self._switch_combo_to_custom()
        self._fill_list_from_ids(ids)

    def _ordered_templates(self) -> list[dict[str, Any]]:
        return sort_phrase_templates(self._templates, mode=self._current_mode())

    def _rebuild_list(self) -> None:
        self._fill_list_from_ids(
            [str(t.get("id") or "") for t in self._ordered_templates()]
        )

    def _fill_list_from_ids(self, ids: list[str]) -> None:
        self._list.clear()
        seen: set[str] = set()
        for pid in ids:
            if not pid or pid in seen:
                continue
            tpl = self._by_id.get(pid)
            if tpl is None:
                continue
            seen.add(pid)
            self._list.addItem(self._make_item(tpl))
        # 欠落があっても全件残す（削除機能はない）
        for tpl in self._templates:
            pid = str(tpl.get("id") or "")
            if pid and pid not in seen:
                seen.add(pid)
                self._list.addItem(self._make_item(tpl))

    def _make_item(self, tpl: dict[str, Any]) -> QListWidgetItem:
        pid = str(tpl.get("id") or "")
        label = phrase_simple_button_label(tpl)
        item = QListWidgetItem(f"⋮⋮  {label}")
        item.setData(Qt.UserRole, pid)
        item.setToolTip(phrase_preview_text(tpl) or label)
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
