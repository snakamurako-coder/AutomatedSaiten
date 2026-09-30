"""① テスト作成ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import (
    clear_active_test,
    create_test,
    delete_test,
    get_test_info,
    list_tests,
    set_active_test,
    update_test,
)
from services.answer_sheet_template import (
    SHEET_TEMPLATE_A4_LANDSCAPE,
    SHEET_TEMPLATE_A4_PORTRAIT,
    export_answer_sheet_templates,
)
from ui_qt import helpers as h


class Step1Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._tests: list[dict[str, Any]] = []
        self._loaded_test_id: str | None = None
        self._form_snapshot: tuple[str, str, str] = ("", "", "")
        # True の間は refresh してもアクティブテストをフォームに載せない（新規作成モード）
        self._draft_new = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(h.title_label("① テスト作成"))

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body)

        form_box = QGroupBox("テスト情報")
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
        action_row = QHBoxLayout()
        self._new_btn = h.button("新規作成", self._on_new_create)
        self._new_btn.setToolTip(
            "入力欄を空にして新規テスト作成モードに切り替えます。"
            "（既存テストは削除されません）"
        )
        self._action_btn = h.button("テストを作成", self._on_action, variant="primary")
        action_row.addWidget(self._new_btn)
        action_row.addWidget(self._action_btn, 1)
        form.addRow(action_row)
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
        self._delete_btn = h.button("削除", self._on_delete, variant="danger-soft")
        self._delete_btn.setToolTip(
            "選択中（または一覧で選んだ）テストを完全に削除します。復元できません。"
        )
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        list_layout.addLayout(btn_row)
        body.addWidget(list_box, 1)

        self.active_label = h.muted_label("選択中: （なし）")
        root.addWidget(self.active_label)

        template_box = QGroupBox("回答用紙ひな形（Excel）")
        template_lay = QVBoxLayout(template_box)
        template_lay.addWidget(
            h.caption_label(
                "GAS 版ハブSSの「テンプレート_共通A4横」「テンプレート_共通A4縦」とほぼ同一の書式です。"
                "生徒IDマーク欄（年/組/番・0〜9）付き。編集して印刷し、スキャン後に ③ 生徒回答用紙取り込みで読み込みます。"
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
        self._rebuild_test_list()
        if self._draft_new:
            # 新規作成モード: 一覧だけ更新し、フォームは空のまま維持
            self.app.active_test_id = None
            self._loaded_test_id = None
            self._sync_action_button()
            self.active_label.setText(
                "選択中: （なし）— テスト名を入力して「テストを作成」"
            )
            return

        active_test = next((t for t in self._tests if t.get("isActive")), None)
        if active_test:
            self.app.active_test_id = active_test["testSsId"]
            self.active_label.setText(f"選択中: {active_test['testName']}")
            self._load_form_for_test_id(active_test["testSsId"])
        else:
            self.app.active_test_id = None
            self.active_label.setText("選択中: （なし）")
            self._clear_form()

        self._sync_action_button()

    def _rebuild_test_list(self) -> None:
        self._tests = list_tests()
        self.test_list.blockSignals(True)
        self.test_list.clear()
        for t in self._tests:
            mark = "● " if t.get("isActive") else "　 "
            self.test_list.addItem(
                f"{mark}{t['testName']}  [{t['status']}] step={t['currentStep']}"
            )
        self.test_list.clearSelection()
        self.test_list.setCurrentRow(-1)
        self.test_list.blockSignals(False)

    def _capture_form_snapshot(self) -> None:
        self._form_snapshot = (
            self.name_edit.text(),
            self.subject_edit.text(),
            self.datetime_edit.text(),
        )

    def _form_has_changes(self) -> bool:
        current = (
            self.name_edit.text(),
            self.subject_edit.text(),
            self.datetime_edit.text(),
        )
        return current != self._form_snapshot

    def _load_form_for_test_id(self, test_id: str) -> None:
        try:
            info = get_test_info(test_id)
        except ValueError:
            self._clear_form()
            return
        self._draft_new = False
        self._loaded_test_id = test_id
        self.name_edit.setText(info.get("testName") or "")
        self.subject_edit.setText(info.get("subject") or "")
        self.datetime_edit.setText(info.get("datetime") or "")
        self._capture_form_snapshot()

    def _clear_form(self) -> None:
        self._loaded_test_id = None
        self.name_edit.blockSignals(True)
        self.subject_edit.blockSignals(True)
        self.datetime_edit.blockSignals(True)
        self.name_edit.setText("")
        self.subject_edit.setText("")
        self.datetime_edit.setText("")
        self.name_edit.blockSignals(False)
        self.subject_edit.blockSignals(False)
        self.datetime_edit.blockSignals(False)
        self._capture_form_snapshot()

    def _sync_action_button(self) -> None:
        if self._loaded_test_id:
            self._action_btn.setText("上書きする")
        else:
            self._action_btn.setText("テストを作成")

    def _on_new_create(self) -> None:
        """前回読み込み／選択中のテストを外し、空の新規作成フォームにする。"""
        has_content = bool(
            self.name_edit.text().strip()
            or self.subject_edit.text().strip()
            or self.datetime_edit.text().strip()
        )
        if has_content and (self._loaded_test_id or self._form_has_changes()):
            ans = QMessageBox.question(
                self,
                "新規作成",
                "入力内容を破棄して空の新規作成フォームにしますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        clear_active_test()
        self.app.active_test_id = None
        self._draft_new = True
        self._clear_form()
        self._rebuild_test_list()
        self._sync_action_button()
        self.active_label.setText(
            "選択中: （なし）— テスト名を入力して「テストを作成」"
        )
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _on_action(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            h.error(self, "入力エラー", "テスト名を入力してください。")
            return
        subject = self.subject_edit.text()
        datetime_str = self.datetime_edit.text()
        if self._loaded_test_id:
            if not self._form_has_changes():
                h.info(self, "変更なし", "テスト情報に変更がありません。")
                return
            try:
                update_test(self._loaded_test_id, name, subject, datetime_str)
                h.info(self, "上書き完了", f"テスト「{name}」を更新しました。")
                self.refresh()
            except Exception as e:
                h.error(self, "エラー", str(e))
            return
        try:
            res = create_test(name, subject, datetime_str)
            self._draft_new = False
            self.app.active_test_id = res["testSsId"]
            h.info(self, "作成完了", f"テスト「{name}」を作成しました。")
            self.refresh()
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_export_answer_templates(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "回答用紙ひな形を Excel 出力",
            "回答用紙ひな形_A4.xlsx",
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
        self._draft_new = False
        set_active_test(test["testSsId"])
        self.app.active_test_id = test["testSsId"]
        self.refresh()

    def _target_test_for_delete(self) -> dict[str, Any] | None:
        """一覧選択を優先し、なければフォームに載っている／アクティブなテスト。"""
        row = self.test_list.currentRow()
        if 0 <= row < len(self._tests):
            return self._tests[row]
        tid = self._loaded_test_id or self.app.active_test_id
        if not tid:
            return None
        for t in self._tests:
            if t.get("testSsId") == tid:
                return t
        return None

    def _on_delete(self) -> None:
        test = self._target_test_for_delete()
        if not test:
            h.warn(
                self,
                "削除",
                "削除するテストを一覧で選択するか、編集中のテストを表示してください。",
            )
            return
        name = str(test.get("testName") or "")
        tid = str(test.get("testSsId") or "")
        ans1 = QMessageBox.question(
            self,
            "テストの削除",
            f"テスト「{name}」を削除しますか？\n\n"
            "採点結果・補正画像・記述欄設定など、このテストの内容はすべて消えます。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans1 != QMessageBox.Yes:
            return
        ans2 = QMessageBox.question(
            self,
            "最終確認 — 復元できません",
            f"本当に「{name}」を削除しますか？\n\n"
            "この操作は取り消せません。削除したデータは二度と再現・復元できません。\n"
            "よろしいですか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans2 != QMessageBox.Yes:
            return
        try:
            delete_test(tid)
            if self.app.active_test_id == tid:
                self.app.active_test_id = None
            if self._loaded_test_id == tid:
                self._draft_new = True
                self._clear_form()
            h.info(self, "削除完了", f"テスト「{name}」を削除しました。")
            self.refresh()
        except Exception as e:
            h.error(self, "削除失敗", str(e))
