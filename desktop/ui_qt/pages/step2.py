"""② 配点決定ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import get_test_info, save_points
from ui_qt import helpers as h


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
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
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
            self.table.setItem(r, 0, QTableWidgetItem(f["id"]))
            self.table.setItem(r, 1, QTableWidgetItem(f["displayName"]))
            self.table.setItem(r, 2, QTableWidgetItem(str(pts)))

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
        try:
            save_points(self.app.active_test_id, self._points_map)
            h.info(self, "保存完了", "配点を保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))
