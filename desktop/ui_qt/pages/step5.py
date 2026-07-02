"""⑤ 採点実行ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.grading import execute_grading, get_summary_data
from ui_qt import helpers as h


class Step5Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑤ 採点の実施"))
        root.addWidget(
            h.muted_label(
                "保存済みの採点基準に従い、全受験者の判定・得点を一括反映し、考査総括を生成します。"
            )
        )

        btn_row = QHBoxLayout()
        btn_row.addWidget(h.button("一括採点を実行", self._on_run, variant="primary"))
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.status_label = h.muted_label("")
        root.addWidget(self.status_label)

        box = QGroupBox("考査総括")
        box_layout = QVBoxLayout(box)
        self.summary_table = QTableWidget(0, 4)
        self.summary_table.setHorizontalHeaderLabels(["区分", "項目", "値", "備考"])
        for i, w in enumerate([90, 260, 110, 300]):
            self.summary_table.setColumnWidth(i, w)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        box_layout.addWidget(self.summary_table)
        root.addWidget(box, 1)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._fill_summary(get_summary_data(self.app.active_test_id))

    def _fill_summary(self, rows: list[dict[str, Any]]) -> None:
        self.summary_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.summary_table.setItem(i, 0, QTableWidgetItem(str(row["category"])))
            self.summary_table.setItem(i, 1, QTableWidgetItem(str(row["item"])))
            self.summary_table.setItem(i, 2, QTableWidgetItem(str(row["value"])))
            self.summary_table.setItem(i, 3, QTableWidgetItem(str(row.get("note", ""))))

    def _on_run(self) -> None:
        if not self.app.require_active_test():
            return
        try:
            res = execute_grading(self.app.active_test_id)
            self.status_label.setText(
                f"採点完了: {res['gradedCount']} 件 / 未登録パターン照合: {res['unregisteredCount']} 件"
            )
            self._fill_summary(get_summary_data(self.app.active_test_id))
            h.info(
                self,
                "採点完了",
                f"{res['gradedCount']} 件を採点しました。\n"
                f"採点基準に無い解答: {res['unregisteredCount']} 件（×・0点として処理）",
            )
        except Exception as e:
            h.error(self, "採点エラー", str(e))
