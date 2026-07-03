"""② 配点決定ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import get_test_info, save_points
from ui_qt import helpers as h
from ui_qt.table_cells import make_editable_item, make_readonly_item, wire_excel_edit_columns


class Step2Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._points_map: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(h.title_label("② 配点決定"))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["記述欄ID", "表示名", "配点"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 220)
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        wire_excel_edit_columns(self.table, (2,), on_changed=self._on_points_cell_changed)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("配点"))
        self.points_edit = QLineEdit()
        self.points_edit.setFixedWidth(80)
        row.addWidget(self.points_edit)
        row.addWidget(h.button("選択行に適用", self._on_apply))
        row.addWidget(h.button("保存", self._on_save, variant="primary"))
        row.addWidget(h.button("再読込", self.refresh))
        row.addStretch()
        root.addLayout(row)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        info = get_test_info(self.app.active_test_id)
        fields = info["fields"]
        points = info["points"]
        self.table.setRowCount(0)
        self._points_map = {}
        for f in fields:
            pts = points.get(f["id"], 0)
            self._points_map[f["id"]] = pts
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, make_readonly_item(f["id"]))
            self.table.setItem(r, 1, make_readonly_item(f["displayName"]))
            self.table.setItem(r, 2, make_editable_item(str(pts), center=True))

    def _on_points_cell_changed(self, row: int, col: int, text: str) -> None:
        if col != 2 or row < 0:
            return
        fid_item = self.table.item(row, 0)
        if fid_item is None:
            return
        try:
            pts = int(text or 0)
        except ValueError:
            return
        self._points_map[fid_item.text()] = pts

    def _sync_points_from_table(self) -> bool:
        for i in range(self.table.rowCount()):
            fid_item = self.table.item(i, 0)
            pts_item = self.table.item(i, 2)
            if fid_item is None or pts_item is None:
                continue
            try:
                pts = int(pts_item.text() or 0)
            except ValueError:
                h.error(self, "入力エラー", f"配点は整数で入力してください（行 {i + 1}）。")
                return False
            self._points_map[fid_item.text()] = pts
        return True

    def _on_apply(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        try:
            pts = int(self.points_edit.text() or 0)
        except ValueError:
            h.error(self, "入力エラー", "配点は整数で入力してください。")
            return
        fid = self.table.item(row, 0).text()
        self._points_map[fid] = pts
        self.table.item(row, 2).setText(str(pts))

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        if not self._sync_points_from_table():
            return
        try:
            save_points(self.app.active_test_id, self._points_map)
            h.info(self, "保存完了", "配点を保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))
