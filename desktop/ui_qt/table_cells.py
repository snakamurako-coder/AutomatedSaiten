"""テーブルセル — ☑/☐ トグル・直接編集用。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

CHECK_MARK = "☑"
UNCHECK_MARK = "☐"


def is_toggle_checked(item: QTableWidgetItem | None) -> bool:
    if item is None:
        return False
    stored = item.data(Qt.ItemDataRole.UserRole)
    if stored is not None:
        return bool(stored)
    return item.text() == CHECK_MARK


def make_toggle_item(checked: bool, *, enabled: bool = True) -> QTableWidgetItem:
    item = QTableWidgetItem(CHECK_MARK if checked else UNCHECK_MARK)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setData(Qt.ItemDataRole.UserRole, checked)
    item.setToolTip("クリックで切替")
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if not enabled:
        flags = Qt.ItemFlag.ItemIsEnabled
    item.setFlags(flags)
    font = item.font()
    font.setPointSize(14)
    item.setFont(font)
    return item


def set_toggle_checked(item: QTableWidgetItem, checked: bool) -> None:
    item.setText(CHECK_MARK if checked else UNCHECK_MARK)
    item.setData(Qt.ItemDataRole.UserRole, checked)


def flip_toggle_item(item: QTableWidgetItem) -> bool:
    checked = not is_toggle_checked(item)
    set_toggle_checked(item, checked)
    return checked


def make_readonly_item(text: str, *, center: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def make_editable_item(text: str, *, center: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setFlags(
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsSelectable
        | Qt.ItemFlag.ItemIsEditable
    )
    return item


def wire_toggle_columns(
    table: QTableWidget,
    columns: tuple[int, ...],
    on_toggle: Callable[[int, int, bool], None],
) -> None:
    """指定列をクリックで ☑/☐ 切替。"""

    def _clicked(row: int, col: int) -> None:
        if col not in columns:
            return
        item = table.item(row, col)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        if item.text() == "—":
            return
        checked = flip_toggle_item(item)
        on_toggle(row, col, checked)

    table.cellClicked.connect(_clicked)


def start_cell_edit(table: QTableWidget, row: int, col: int) -> None:
    item = table.item(row, col)
    if item and item.flags() & Qt.ItemFlag.ItemIsEditable:
        table.setCurrentCell(row, col)
        table.editItem(item)


def wire_excel_edit_columns(
    table: QTableWidget,
    columns: tuple[int, ...],
    *,
    on_changed: Callable[[int, int, str], None] | None = None,
) -> None:
    """指定列をクリックで即編集（Excel 風）。"""

    def _clicked(row: int, col: int) -> None:
        if col not in columns:
            return
        start_cell_edit(table, row, col)

    table.cellClicked.connect(_clicked)

    if on_changed is not None:
        def _changed(item: QTableWidgetItem) -> None:
            if item.column() in columns:
                on_changed(item.row(), item.column(), item.text().strip())

        table.itemChanged.connect(_changed)
