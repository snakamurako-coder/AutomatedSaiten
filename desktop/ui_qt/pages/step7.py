"""⑦ 名簿割当・領域合計・外部連携得点ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.domain_repo import calculate_domain_scores
from models.roster_repo import (
    ROSTER_MAPPING_FIELDS,
    assign_ids_from_roster,
    get_id_assignment_status,
    get_roster_absent_state,
    get_roster_assignment_preview,
    get_roster_rows,
    get_selected_roster_name,
    import_external_scores,
    import_roster_with_mapping,
    list_roster_names,
    parse_external_scores_csv,
    parse_roster_tsv,
    save_roster_absent_state,
    save_selected_roster_name,
)
from ui_qt import helpers as h


class RosterImportDialog(QDialog):
    """TSV 貼り付け → 列マッピング → 名簿登録。"""

    def __init__(self, parent: QWidget, raw_rows: list[list[str]], col_count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("名簿の列マッピング")
        self.resize(720, 480)
        self._raw_rows = raw_rows
        self.imported_name: str | None = None

        root = QVBoxLayout(self)
        root.addWidget(h.title_label("名簿の列マッピング"))

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名簿名"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: 3年A組")
        name_row.addWidget(self.name_edit, 1)
        self.skip_header_check = QCheckBox("1行目はヘッダー（取込対象外）")
        self.skip_header_check.setChecked(True)
        name_row.addWidget(self.skip_header_check)
        root.addLayout(name_row)

        root.addWidget(h.caption_label("各列をどの項目として取り込むか選択してください。"))

        self.preview = QTableWidget(0, col_count)
        self.combos: list[QComboBox] = []
        options = [("ignore", "（無視）")] + list(ROSTER_MAPPING_FIELDS)
        header_labels = []
        for c in range(col_count):
            header_labels.append(f"列{c + 1}")
        self.preview.setHorizontalHeaderLabels(header_labels)

        combo_row = QHBoxLayout()
        for c in range(col_count):
            combo = QComboBox()
            for key, label in options:
                combo.addItem(label, key)
            # 既定推測: ID, 年, 組, 番号, 氏名 の順
            if c < len(ROSTER_MAPPING_FIELDS):
                combo.setCurrentIndex(c + 1)
            self.combos.append(combo)
            combo_row.addWidget(combo, 1)
        root.addLayout(combo_row)

        rows = raw_rows[:8]
        self.preview.setRowCount(len(rows))
        for i, cells in enumerate(rows):
            for c in range(col_count):
                self.preview.setItem(
                    i, c, QTableWidgetItem(cells[c] if c < len(cells) else "")
                )
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.preview, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(h.button("キャンセル", self.reject))
        btns.addWidget(h.button("名簿に登録", self._on_import, variant="primary"))
        root.addLayout(btns)

    def _on_import(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            h.warn(self, "入力不足", "名簿名を入力してください。")
            return
        mapping = {i: combo.currentData() for i, combo in enumerate(self.combos)}
        try:
            count = import_roster_with_mapping(
                name,
                self._raw_rows,
                mapping,
                skip_first_row=self.skip_header_check.isChecked(),
            )
            self.imported_name = name
            h.info(self, "登録完了", f"名簿「{name}」に {count} 名を登録しました。")
            self.accept()
        except Exception as e:
            h.error(self, "エラー", str(e))


class Step7Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._roster_rows: list[dict[str, Any]] = []
        self._absent_keys: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑦ 合計・外部得点"))

        root.addWidget(self._build_roster_box())
        root.addWidget(self._build_score_box())
        root.addStretch()

    # ---------- 名簿割当 ----------

    def _build_roster_box(self) -> QGroupBox:
        box = QGroupBox("名簿連動（ID・氏名の割り当て）")
        lay = QVBoxLayout(box)
        self.assign_status_label = h.muted_label("")
        lay.addWidget(self.assign_status_label)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("名簿"))
        self.roster_combo = QComboBox()
        self.roster_combo.setMinimumWidth(200)
        self.roster_combo.currentTextChanged.connect(self._on_roster_changed)
        ctrl.addWidget(self.roster_combo)
        ctrl.addWidget(h.button("名簿を表示し未受験者を入力", self._on_show_roster))
        self.assign_btn = h.button("ID・氏名を割り当て", self._on_assign, variant="primary")
        self.assign_btn.setEnabled(False)
        ctrl.addWidget(self.assign_btn)
        ctrl.addWidget(h.button("TSV から名簿を登録…", self._on_paste_roster))
        ctrl.addStretch()
        lay.addLayout(ctrl)

        self.assign_summary_label = h.caption_label("")
        lay.addWidget(self.assign_summary_label)

        self.roster_table = QTableWidget(0, 5)
        self.roster_table.setHorizontalHeaderLabels(["未受験", "組", "番号", "ID", "氏名"])
        for i, w in enumerate([56, 60, 60, 110, 200]):
            self.roster_table.setColumnWidth(i, w)
        self.roster_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.roster_table.setFixedHeight(220)
        self.roster_table.cellClicked.connect(self._on_roster_cell_clicked)
        self.roster_table.setVisible(False)
        lay.addWidget(self.roster_table)
        return box

    def _build_score_box(self) -> QGroupBox:
        box = QGroupBox("得点計算")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(h.button("領域合計・総計点を再計算", self._on_recalc, variant="primary"))
        row.addStretch()
        lay.addLayout(row)

        lay.addWidget(
            h.caption_label(
                "外部得点（マーク式など別システムの得点）を「生徒ID,得点[,ソース]」の形式で貼り付けて取り込みます。"
                "総計点 = 記述欄得点の合計 + 外部得点。"
            )
        )
        self.external_edit = QPlainTextEdit()
        self.external_edit.setPlaceholderText("例:\n1001,45\n1002,38,マークシート")
        self.external_edit.setFixedHeight(110)
        lay.addWidget(self.external_edit)
        ext_row = QHBoxLayout()
        ext_row.addWidget(h.button("外部得点を取込", self._on_import_external, variant="success"))
        ext_row.addStretch()
        lay.addLayout(ext_row)

        self.score_status_label = h.caption_label("")
        lay.addWidget(self.score_status_label)
        return box

    # ---------- 再読込 ----------

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        names = list_roster_names()
        selected = get_selected_roster_name(self.app.active_test_id)
        self.roster_combo.blockSignals(True)
        self.roster_combo.clear()
        self.roster_combo.addItem("")
        self.roster_combo.addItems(names)
        if selected and selected in names:
            self.roster_combo.setCurrentText(selected)
        self.roster_combo.blockSignals(False)
        self._update_assign_status()

        # 保存済みの未受験者状態を復元
        state = get_roster_absent_state(self.app.active_test_id)
        self._absent_keys = {
            f"id:{a.get('studentId')}" if a.get("studentId") else f"name:{a.get('name')}"
            for a in state.get("absentStudents") or []
        }
        # 外部得点の既存データを表示
        from models.roster_repo import get_external_scores

        ext = get_external_scores(self.app.active_test_id)
        if ext:
            self.external_edit.setPlainText(
                "\n".join(f"{e['studentId']},{e['score']},{e['source']}" for e in ext)
            )

    def _update_assign_status(self) -> None:
        st = get_id_assignment_status(self.app.active_test_id)
        if st["skipAssignment"]:
            msg = (
                f"IDマーク欄から {st['withIdCount']}/{st['resultCount']} 件の ID を取得済みのため、"
                "名簿からの割り当ては不要です。"
            )
        elif st["resultCount"] == 0:
            msg = "採点結果がまだありません。③ テキスト化を先に実行してください。"
        else:
            msg = (
                f"解答 {st['resultCount']} 件中 ID 入力済み {st['withIdCount']} 件。"
                "名簿を選択し、未受験者を除外してから割り当ててください（ファイル名順 ↔ 名簿の組・番号順で 1:1 対応）。"
            )
        self.assign_status_label.setText(msg)

    # ---------- 名簿操作 ----------

    def _on_roster_changed(self, name: str) -> None:
        if not self.app.active_test_id:
            return
        save_selected_roster_name(self.app.active_test_id, name)
        self.roster_table.setVisible(False)
        self.assign_btn.setEnabled(False)
        self._absent_keys = set()

    def _on_show_roster(self) -> None:
        name = self.roster_combo.currentText().strip()
        if not name:
            h.warn(self, "名簿未選択", "名簿を選択してください。")
            return
        self._roster_rows = get_roster_rows(name)
        if not self._roster_rows:
            h.warn(self, "名簿なし", f"名簿「{name}」にデータがありません。")
            return
        self._render_roster_table()
        self.roster_table.setVisible(True)
        self.assign_btn.setEnabled(True)
        self._update_assign_summary()

    def _render_roster_table(self) -> None:
        t = self.roster_table
        t.setRowCount(len(self._roster_rows))
        for i, r in enumerate(self._roster_rows):
            key = f"id:{r['studentId']}" if r["studentId"] else f"name:{r['name']}"
            absent = key in self._absent_keys
            check_item = QTableWidgetItem("☑" if absent else "☐")
            check_item.setTextAlignment(Qt.AlignCenter)
            t.setItem(i, 0, check_item)
            t.setItem(i, 1, QTableWidgetItem(r["classNo"]))
            t.setItem(i, 2, QTableWidgetItem(r["number"]))
            t.setItem(i, 3, QTableWidgetItem(r["studentId"]))
            t.setItem(i, 4, QTableWidgetItem(r["name"]))

    def _on_roster_cell_clicked(self, row: int, col: int) -> None:
        if col != 0 or row >= len(self._roster_rows):
            return
        r = self._roster_rows[row]
        key = f"id:{r['studentId']}" if r["studentId"] else f"name:{r['name']}"
        if key in self._absent_keys:
            self._absent_keys.discard(key)
        else:
            self._absent_keys.add(key)
        self._render_roster_table()
        self._update_assign_summary()
        self._save_absent_state()

    def _absent_students(self) -> list[dict[str, str]]:
        out = []
        for r in self._roster_rows:
            key = f"id:{r['studentId']}" if r["studentId"] else f"name:{r['name']}"
            if key in self._absent_keys:
                out.append({"studentId": r["studentId"], "name": r["name"]})
        return out

    def _save_absent_state(self) -> None:
        save_roster_absent_state(
            self.app.active_test_id,
            self.roster_combo.currentText().strip(),
            self._absent_students(),
        )

    def _update_assign_summary(self) -> None:
        total = len(self._roster_rows)
        absent = len(self._absent_keys)
        self.assign_summary_label.setText(
            f"名簿 {total} 名 / 未受験 {absent} 名 / 受験予定 {total - absent} 名"
        )

    def _on_assign(self) -> None:
        name = self.roster_combo.currentText().strip()
        if not name or not self._roster_rows:
            h.warn(self, "未準備", "名簿を選択し「名簿を表示し未受験者を入力」を押してください。")
            return
        absent = self._absent_students()
        preview = get_roster_assignment_preview(self.app.active_test_id, name, absent)
        if not preview["match"]:
            h.error(
                self,
                "件数不一致",
                f"解答 {preview['resultCount']} 件 / 受験予定 {preview['expectedCount']} 名で一致しません。\n"
                "未受験者の指定を確認してください。",
            )
            return
        try:
            res = assign_ids_from_roster(self.app.active_test_id, name, absent)
            if res.get("skipped"):
                h.info(self, "スキップ", res.get("message", ""))
            else:
                h.info(self, "割当完了", f"{res['assigned']} 名の ID・氏名を割り当てました。")
            self._update_assign_status()
        except Exception as e:
            h.error(self, "割当エラー", str(e))

    def _on_paste_roster(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("名簿 TSV 貼り付け")
        dlg.resize(560, 360)
        lay = QVBoxLayout(dlg)
        lay.addWidget(h.muted_label("Excel などから名簿をコピーして貼り付けてください（タブ/カンマ区切り）。"))
        text = QPlainTextEdit()
        lay.addWidget(text, 1)
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(h.button("キャンセル", dlg.reject))

        def go_mapping() -> None:
            parsed = parse_roster_tsv(text.toPlainText())
            if not parsed["rows"]:
                h.warn(dlg, "入力なし", "名簿データを貼り付けてください。")
                return
            dlg.accept()
            map_dlg = RosterImportDialog(self, parsed["rows"], parsed["colCount"])
            if map_dlg.exec() == QDialog.Accepted and map_dlg.imported_name:
                self.refresh()
                self.roster_combo.setCurrentText(map_dlg.imported_name)

        btns.addWidget(h.button("列マッピングへ", go_mapping, variant="primary"))
        lay.addLayout(btns)
        dlg.exec()

    # ---------- 得点計算 ----------

    def _on_recalc(self) -> None:
        if not self.app.require_active_test():
            return
        try:
            updated = calculate_domain_scores(self.app.active_test_id)
            self.score_status_label.setText(f"{updated} 件の領域合計・総計点を再計算しました。")
            h.info(self, "再計算完了", f"{updated} 件の領域合計・総計点を再計算しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_import_external(self) -> None:
        if not self.app.require_active_test():
            return
        rows = parse_external_scores_csv(self.external_edit.toPlainText())
        if not rows:
            h.warn(self, "形式エラー", "「生徒ID,得点[,ソース]」の形式で入力してください。")
            return
        try:
            count = import_external_scores(self.app.active_test_id, rows)
            self.score_status_label.setText(f"外部得点 {count} 件を取り込み、総計点を再計算しました。")
            h.info(self, "取込完了", f"外部得点 {count} 件を取り込みました。")
        except Exception as e:
            h.error(self, "エラー", str(e))
