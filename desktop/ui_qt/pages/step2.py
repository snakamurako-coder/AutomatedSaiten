"""② 配点決定ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import get_test_info, save_answer_fields, save_points
from ui_qt import helpers as h
from ui_qt.style import COLORS
from ui_qt.table_cells import make_editable_item, make_readonly_item, wire_excel_edit_columns


class Step2Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._points_map: dict[str, int] = {}
        self._fields: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(h.title_label("② 配点決定"))
        header.addStretch()
        self.total_label = QLabel("合計点: 0")
        self.total_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.total_label.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {COLORS['accent']};"
            f" padding: 4px 10px; background: {COLORS['accent_soft']};"
            f" border: 1px solid #93c5fd; border-radius: 8px;"
        )
        header.addWidget(self.total_label)
        root.addLayout(header)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["記述欄ID", "表示名", "配点"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 220)
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        wire_excel_edit_columns(self.table, (1, 2), on_changed=self._on_table_cell_changed)
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
        self._fields = [dict(f) for f in fields]
        self.table.setRowCount(0)
        self._points_map = {}
        for f in self._fields:
            pts = points.get(f["id"], 0)
            self._points_map[f["id"]] = pts
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, make_readonly_item(f["id"]))
            self.table.setItem(r, 1, make_editable_item(f["displayName"]))
            self.table.setItem(r, 2, make_editable_item(str(pts), center=True))
        self._update_total_label()

    def _update_total_label(self) -> None:
        total = 0
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 2)
            if item is None:
                continue
            try:
                total += int(item.text() or 0)
            except ValueError:
                continue
        self.total_label.setText(f"合計点: {total}")

    def _on_table_cell_changed(self, row: int, col: int, text: str) -> None:
        if row < 0:
            return
        fid_item = self.table.item(row, 0)
        if fid_item is None:
            return
        fid = fid_item.text()
        if col == 1:
            name = text.strip() or fid
            for f in self._fields:
                if f["id"] == fid:
                    f["displayName"] = name
                    break
        elif col == 2:
            try:
                pts = int(text or 0)
            except ValueError:
                self._update_total_label()
                return
            self._points_map[fid] = pts
            self._update_total_label()

    def _sync_from_table(self) -> bool:
        for i in range(self.table.rowCount()):
            fid_item = self.table.item(i, 0)
            name_item = self.table.item(i, 1)
            pts_item = self.table.item(i, 2)
            if fid_item is None or name_item is None or pts_item is None:
                continue
            fid = fid_item.text()
            name = name_item.text().strip() or fid
            try:
                pts = int(pts_item.text() or 0)
            except ValueError:
                h.error(self, "入力エラー", f"配点は整数で入力してください（行 {i + 1}）。")
                return False
            for f in self._fields:
                if f["id"] == fid:
                    f["displayName"] = name
                    break
            self._points_map[fid] = pts
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
        self._update_total_label()

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        if not self._sync_from_table():
            return
        test_id = self.app.active_test_id
        try:
            save_answer_fields(test_id, self._fields)
            save_points(test_id, self._points_map)
            h.info(self, "保存完了", "表示名と配点を保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))
