"""⓪ テスト作成ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import create_test, list_tests, set_active_test
from ui_qt import helpers as h


class Step0Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._tests: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(h.title_label("⓪ テスト作成"))

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body)

        form_box = QGroupBox("新規テスト")
        form = QFormLayout(form_box)
        form.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: 2026前期 中間テスト")
        self.subject_edit = QLineEdit()
        self.datetime_edit = QLineEdit()
        self.datetime_edit.setPlaceholderText("例: 2026-07-02 10:00")
        form.addRow("テスト名 *", self.name_edit)
        form.addRow("科目名", self.subject_edit)
        form.addRow("実施日時", self.datetime_edit)
        form.addRow(h.button("テストを作成", self._on_create, variant="primary"))
        form_box.setFixedWidth(360)
        body.addWidget(form_box, 0)

        list_box = QGroupBox("テスト一覧")
        list_layout = QVBoxLayout(list_box)
        self.test_list = QListWidget()
        self.test_list.itemDoubleClicked.connect(lambda _i: self._on_select())
        list_layout.addWidget(self.test_list)
        btn_row = QHBoxLayout()
        btn_row.addWidget(h.button("選択", self._on_select))
        btn_row.addWidget(h.button("更新", self.refresh))
        btn_row.addStretch()
        list_layout.addLayout(btn_row)
        body.addWidget(list_box, 1)

        self.active_label = h.muted_label("選択中: （なし）")
        root.addWidget(self.active_label)
        root.addStretch()

    def refresh(self) -> None:
        self._tests = list_tests()
        self.test_list.clear()
        for t in self._tests:
            mark = "● " if t.get("isActive") else "　 "
            self.test_list.addItem(f"{mark}{t['testName']}  [{t['status']}] step={t['currentStep']}")
        if self._tests:
            active = next((t for t in self._tests if t.get("isActive")), self._tests[0])
            self.app.active_test_id = active["testSsId"]
            self.active_label.setText(f"選択中: {active['testName']}")
        else:
            self.app.active_test_id = None
            self.active_label.setText("選択中: （なし）")

    def _on_create(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            h.error(self, "入力エラー", "テスト名を入力してください。")
            return
        try:
            res = create_test(name, self.subject_edit.text(), self.datetime_edit.text())
            self.app.active_test_id = res["testSsId"]
            h.info(self, "作成完了", f"テスト「{name}」を作成しました。")
            self.refresh()
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_select(self) -> None:
        row = self.test_list.currentRow()
        if row < 0 or row >= len(self._tests):
            return
        test = self._tests[row]
        set_active_test(test["testSsId"])
        self.app.active_test_id = test["testSsId"]
        self.refresh()
