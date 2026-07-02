"""⑥ 領域設定ページ（記述欄を大問・範囲・能力にグルーピング）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.domain_repo import (
    calculate_domain_scores,
    get_domain_settings_for_ui,
    save_domain_settings,
)
from ui_qt import helpers as h


class Step6Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑥ 領域の設定"))
        root.addWidget(
            h.muted_label(
                "記述欄を「大問」「範囲」「能力」のラベルでグルーピングすると、"
                "領域別の得点が集計され、考査総括・個票に反映されます。"
                "同じラベルを付けた記述欄が 1 つの領域として合算されます。"
            )
        )

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["記述欄", "大問", "範囲", "能力"])
        self.table.setColumnWidth(0, 220)
        for c in (1, 2, 3):
            self.table.setColumnWidth(c, 140)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addWidget(
            h.button("領域設定を保存・再計算", self._on_save, variant="primary")
        )
        btns.addWidget(h.button("再読込", self.refresh))
        btns.addStretch()
        root.addLayout(btns)

        self.status_label = h.caption_label("")
        root.addWidget(self.status_label)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._rows = get_domain_settings_for_ui(self.app.active_test_id)
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            name_item = QTableWidgetItem(row["displayName"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, QTableWidgetItem(row["daiMon"]))
            self.table.setItem(i, 2, QTableWidgetItem(row["hanI"]))
            self.table.setItem(i, 3, QTableWidgetItem(row["noryoku"]))
        if not self._rows:
            self.status_label.setText("記述欄がありません。先に ① 回答欄設定を完了してください。")
        else:
            self.status_label.setText("")

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        settings = []
        for i, row in enumerate(self._rows):
            settings.append(
                {
                    "fieldId": row["fieldId"],
                    "daiMon": self._cell_text(i, 1),
                    "hanI": self._cell_text(i, 2),
                    "noryoku": self._cell_text(i, 3),
                }
            )
        try:
            save_domain_settings(self.app.active_test_id, settings)
            updated = calculate_domain_scores(self.app.active_test_id)
            self.status_label.setText(f"領域設定を保存し、{updated} 件の得点を再計算しました。")
            h.info(self, "保存完了", f"領域設定を保存し、{updated} 件の得点を再計算しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""
