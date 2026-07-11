"""⓪ テスト作成ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import create_test, list_tests, set_active_test
from services.answer_sheet_template import (
    SHEET_TEMPLATE_A4_LANDSCAPE,
    SHEET_TEMPLATE_A4_PORTRAIT,
    export_answer_sheet_templates,
)
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

        template_box = QGroupBox("解答用紙ひな形（Excel）")
        template_lay = QVBoxLayout(template_box)
        template_lay.addWidget(
            h.caption_label(
                "GAS 版ハブSSの「テンプレート_共通A4横」「テンプレート_共通A4縦」とほぼ同一の書式です。"
                "生徒IDマーク欄（年/組/番・0〜9）付き。編集して印刷し、スキャン後に ③ で読み込みます。"
            )
        )
        tpl_btns = QHBoxLayout()
        tpl_btns.addWidget(
            h.button(
                "A4横・A4縦ひな形を Excel 出力",
                self._on_export_answer_templates,
                variant="primary",
            )
        )
        tpl_btns.addWidget(
            h.open_folder_button(self._on_open_template_folder, text="出力フォルダを開く")
        )
        tpl_btns.addStretch()
        template_lay.addLayout(tpl_btns)
        root.addWidget(template_box)
        root.addStretch()
        self._last_template_path: str | None = None

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

    def _on_export_answer_templates(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "解答用紙ひな形を Excel 出力",
            "解答用紙ひな形_A4.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            saved = export_answer_sheet_templates(path)
            self._last_template_path = saved
            h.info(
                self,
                "出力完了",
                f"保存しました:\n{saved}\n\n"
                f"シート「{SHEET_TEMPLATE_A4_LANDSCAPE}」「{SHEET_TEMPLATE_A4_PORTRAIT}」"
                "（生徒IDマーク欄付き）を編集・印刷してください。",
            )
        except Exception as e:
            h.error(self, "出力失敗", str(e))

    def _on_open_template_folder(self) -> None:
        if self._last_template_path:
            h.open_in_file_manager(self._last_template_path, parent=self)
            return
        h.warn(self, "出力フォルダ", "先に「Excel 出力」でファイルを保存してください。")

    def _on_select(self) -> None:
        row = self.test_list.currentRow()
        if row < 0 or row >= len(self._tests):
            return
        test = self._tests[row]
        set_active_test(test["testSsId"])
        self.app.active_test_id = test["testSsId"]
        self.refresh()
