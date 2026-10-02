"""⑪ 名簿割当・領域合計・外部連携得点ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.domain_repo import calculate_domain_scores
from models.roster_repo import (
    _norm_person_name,
    ROSTER_MAPPING_FIELDS,
    assign_ids_from_roster,
    compare_external_scores,
    get_id_assignment_status,
    get_roster_absent_state,
    get_roster_assignment_preview,
    get_roster_rows,
    get_selected_roster_name,
    import_external_scores,
    import_roster_absent_from_test,
    import_roster_with_mapping,
    list_roster_absent_import_sources,
    list_roster_names,
    parse_external_scores_csv,
    parse_roster_tsv,
    save_roster_absent_state,
    save_selected_roster_name,
)
from ui_qt import helpers as h
from ui_qt.layout_helpers import make_expanding
from ui_qt.style import COLORS


class ExternalScoreMatchDialog(QDialog):
    """外部の ID・氏名・得点を、本体採点の ID・氏名と突き合わせて確認する。"""

    importRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("外部得点の照合")
        self.resize(1040, 520)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        h.enable_dialog_maximize(self)
        self.accepted_import = False
        self.test_id = ""
        self.feed_rows: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []
        self._skip_boxes: list[QCheckBox | None] = []
        self._sheet_dialog: QDialog | None = None

        lay = QVBoxLayout(self)
        note = QLabel(
            "基準は ID です。氏名は空白と記号を除いて比べます。"
            "違う行の氏名は赤で示します。外字や異体字で字面だけ違うときは「スルー」にすると、その違いは確認済みになります。"
            "このウィンドウは開いたまま、他のステップも操作できます。"
            "⑬で「修正を保存」すると、ID・氏名と照合結果を更新します。"
            "「元画像」でその答案の全体を確認できます。"
            "取り込むと、このテストの外部得点をこの一覧の ID で置き換えます。"
            "同じ ID が複数あるときは、最後の得点を採用します。"
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        self._summary = QLabel("")
        lay.addWidget(self._summary)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["照合", "ID", "本体の氏名", "外部の氏名", "得点", "スルー", "元画像"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        buttons.addWidget(h.button("氏名の違いをすべてスルー", self._skip_all_name_diffs))
        buttons.addStretch()
        buttons.addWidget(h.button("閉じる", self.reject))
        buttons.addWidget(h.button("この内容で取り込む", self._accept_import, variant="primary"))
        lay.addLayout(buttons)

    def set_comparison(
        self,
        test_id: str,
        feed_rows: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> None:
        self.test_id = test_id
        self.feed_rows = list(feed_rows)
        self.accepted_import = False
        self._populate(rows, self._skipped_ids())

    def reload(self) -> None:
        """本体の ID・氏名を読み直し、照合と不一致表示を更新する。"""
        if not self.test_id:
            return
        skipped = self._skipped_ids()
        try:
            compared = compare_external_scores(self.test_id, self.feed_rows)
        except Exception as e:
            h.error(self, "照合エラー", str(e))
            return
        self._populate(compared, skipped)

    def _skipped_ids(self) -> set[str]:
        ids: set[str] = set()
        for index, row in enumerate(self._rows):
            box = self._skip_boxes[index] if index < len(self._skip_boxes) else None
            if box is not None and box.isChecked():
                sid = str(row.get("studentId") or "").strip()
                if sid:
                    ids.add(sid)
        return ids

    def _populate(self, rows: list[dict[str, Any]], skipped_ids: set[str]) -> None:
        self._rows = list(rows)
        self._skip_boxes = []
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            score = row.get("score")
            score_text = "" if score is None else str(score)
            values = [
                str(row.get("status") or ""),
                str(row.get("studentId") or ""),
                str(row.get("bodyName") or ""),
                str(row.get("feedName") or ""),
                score_text,
                "",
            ]
            for col, text in enumerate(values):
                self._table.setItem(i, col, QTableWidgetItem(text))
            if row.get("nameDiffers"):
                box = QCheckBox("スルー")
                sid = str(row.get("studentId") or "").strip()
                if sid in skipped_ids:
                    box.setChecked(True)
                box.toggled.connect(lambda _checked, index=i: self._on_skip_toggled(index))
                wrap = QWidget()
                box_lay = QHBoxLayout(wrap)
                box_lay.setContentsMargins(8, 0, 8, 0)
                box_lay.addWidget(box)
                box_lay.addStretch()
                self._table.setCellWidget(i, 5, wrap)
                self._skip_boxes.append(box)
            else:
                self._skip_boxes.append(None)
            open_btn = QPushButton("元画像")
            open_btn.clicked.connect(lambda _checked=False, index=i: self._open_original(index))
            self._table.setCellWidget(i, 6, open_btn)
            self._paint_row(i)
        self._table.resizeColumnsToContents()
        self._table.setColumnWidth(0, 180)
        self._table.setColumnWidth(5, 90)
        self._table.setColumnWidth(6, 88)
        self._refresh_summary()

    def reject(self) -> None:  # noqa: N802
        self.hide()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hide()
        event.ignore()

    def _paint_row(self, index: int) -> None:
        row = self._rows[index]
        status = str(row.get("status") or "")
        skipped = self._skip_boxes[index] is not None and self._skip_boxes[index].isChecked()
        name_differs = bool(row.get("nameDiffers"))
        warn_bg = QColor("#fef3c7")
        miss_bg = QColor(COLORS["danger_soft"])
        name_bg = QColor("#fecaca")
        name_fg = QColor("#991b1b")
        skip_bg = QColor(COLORS["success_soft"])
        plain = QColor(COLORS["surface"])
        text = QColor(COLORS["text"])
        row_bg = plain
        if status != "一致" and not (name_differs and skipped and status == "氏名が違う"):
            row_bg = miss_bg if "ない" in status else warn_bg
        if name_differs and skipped and "ない" not in status and "重複" not in status and "複数" not in status:
            row_bg = skip_bg
        status_item = self._table.item(index, 0)
        if status_item is not None:
            status_item.setText(self._status_text(status, name_differs and skipped))
        for col in range(5):
            item = self._table.item(index, col)
            if item is None:
                continue
            highlight_name = name_differs and not skipped and col in (0, 2, 3)
            if highlight_name:
                item.setBackground(name_bg)
                item.setForeground(name_fg)
            else:
                item.setBackground(row_bg)
                item.setForeground(text)

    @staticmethod
    def _status_text(status: str, skipped: bool) -> str:
        if not skipped:
            return status
        rest = status.replace("氏名が違う・", "").replace("氏名が違う", "").strip("・")
        if not rest:
            return "スルー"
        return f"スルー・{rest}"

    def _on_skip_toggled(self, index: int) -> None:
        self._paint_row(index)
        self._refresh_summary()

    def _skip_all_name_diffs(self) -> None:
        for box in self._skip_boxes:
            if box is not None:
                box.setChecked(True)

    def _refresh_summary(self) -> None:
        matched = 0
        name_diff = 0
        skipped = 0
        other = 0
        for index, row in enumerate(self._rows):
            box = self._skip_boxes[index]
            if row.get("nameDiffers"):
                if box is not None and box.isChecked():
                    skipped += 1
                else:
                    name_diff += 1
            elif row.get("status") == "一致":
                matched += 1
            else:
                other += 1
        self._summary.setText(
            f"一致 {matched} 件 / 氏名が違う {name_diff} 件 / スルー {skipped} 件 / その他 {other} 件"
        )

    def _accept_import(self) -> None:
        self.accepted_import = True
        self.importRequested.emit()

    def _find_result(self, row: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        from models.test_repo import get_all_results

        results = get_all_results(self.test_id)
        sid = str(row.get("studentId") or "").strip()
        if sid:
            by_id = [
                item
                for item in results
                if str(item.get("studentId") or "").strip() == sid
            ]
            if len(by_id) == 1:
                return by_id[0], ""
            if len(by_id) > 1:
                return None, f"ID {sid} の答案が複数あります。"
        keys = {
            _norm_person_name(part)
            for part in str(row.get("bodyName") or "").split("/")
            if _norm_person_name(part)
        }
        by_name = [
            item
            for item in results
            if keys and _norm_person_name(str(item.get("name") or "")) in keys
        ]
        if len(by_name) == 1:
            return by_name[0], ""
        if len(by_name) > 1:
            return None, "同じ氏名の答案が複数あります。"
        return None, "この行に対応する答案が見つかりません。"

    def _open_original(self, index: int) -> None:
        from services.crop_preview import resolve_source_path
        from ui_qt.full_sheet_grade_dialog import FullSheetGradeDialog

        if index < 0 or index >= len(self._rows):
            return
        result, message = self._find_result(self._rows[index])
        if result is None:
            h.warn(self, "元画像", message)
            return
        try:
            path = resolve_source_path(result, test_id=self.test_id)
        except FileNotFoundError as e:
            h.warn(self, "元画像", str(e))
            return
        old = self._sheet_dialog
        self._sheet_dialog = None
        if old is not None:
            old.close()
        dialog = FullSheetGradeDialog(
            self.parent() or self,
            test_id=self.test_id,
            result_row=result,
            warped_path=path,
            fields=[],
            points={},
            original_view=True,
        )
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._sheet_dialog = dialog
        dialog.destroyed.connect(lambda _obj=None, current=dialog: self._forget_sheet(current))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_sheet(self, dialog: QDialog) -> None:
        if self._sheet_dialog is dialog:
            self._sheet_dialog = None


class RosterImportDialog(QDialog):
    """TSV 貼り付け → 列マッピング → 名簿登録。"""

    def __init__(self, parent: QWidget, raw_rows: list[list[str]], col_count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("名簿の列マッピング")
        self.resize(720, 480)
        h.enable_dialog_maximize(self)
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


class RosterImportFromTestDialog(QDialog):
    """他テストから名簿・未受験者設定を選んでインポートする。"""

    def __init__(self, parent: QWidget, sources: list[dict[str, Any]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("名簿・未受験者のインポート")
        self.resize(640, 360)
        h.enable_dialog_maximize(self)
        self._sources = sources
        self.selected_test_id: str | None = None

        root = QVBoxLayout(self)
        root.addWidget(
            h.muted_label(
                "名簿と未受験者（☑）が保存されているテストを選ぶと、"
                "「名簿を表示し未受験者を入力」と同じ状態を反映します。"
            )
        )
        self.list = QListWidget()
        for s in sources:
            item_text = (
                f"{s['testName']} — 名簿「{s['rosterName']}」"
                f"（{s['rosterCount']} 名 / 未受験 {s['absentCount']} 名）"
            )
            if s.get("savedAt"):
                item_text += f"  [{s['savedAt']}]"
            self.list.addItem(item_text)
        if sources:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        root.addWidget(self.list, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(h.button("キャンセル", self.reject))
        btns.addWidget(h.button("反映", self._accept_selection, variant="primary"))
        root.addLayout(btns)

    def _accept_selection(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._sources):
            h.warn(self, "未選択", "インポート元のテストを選んでください。")
            return
        self.selected_test_id = str(self._sources[row]["testId"])
        self.accept()


class Step11Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._roster_rows: list[dict[str, Any]] = []
        self._absent_keys: set[str] = set()
        self._external_dialog: ExternalScoreMatchDialog | None = None

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.addWidget(h.title_label("⑪ 合計点・外部得点"))
        title_row.addStretch()
        self.import_roster_btn = h.button(
            "名簿と未受験者が一致するテストからインポート（反映）",
            self._on_import_roster_from_test,
        )
        self.import_roster_btn.setToolTip(
            "他テストで保存済みの名簿選択・未受験☑を取り込みます。"
            "（IDマーク欄から ID 取得済みの場合は不要）"
        )
        title_row.addWidget(self.import_roster_btn)
        root.addLayout(title_row)

        root.addWidget(self._build_roster_box(), 2)
        root.addWidget(self._build_score_box(), 1)

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

        order_row = QHBoxLayout()
        order_row.setSpacing(6)
        order_row.addWidget(h.caption_label("紐づけ順"))
        self._file_order_lbl_asc = QLabel("ファイル名昇順")
        self._file_order_lbl_desc = QLabel("ファイル名降順")
        self.file_order_switch = QCheckBox()
        self.file_order_switch.setObjectName("RosterFileOrderSwitch")
        self.file_order_switch.setChecked(False)
        self.file_order_switch.setCursor(Qt.PointingHandCursor)
        self.file_order_switch.setToolTip(
            "OFF（既定）: 生徒ID昇順 ↔ ファイル名昇順（表スキャン向け）\n"
            "ON: 生徒ID昇順 ↔ ファイル名降順（裏スキャンでIDが逆順になる場合）"
        )
        self.file_order_switch.setStyleSheet(
            f"""
            QCheckBox#RosterFileOrderSwitch {{
                spacing: 0px;
            }}
            QCheckBox#RosterFileOrderSwitch::indicator {{
                width: 40px; height: 22px; border-radius: 11px;
                border: 1px solid {COLORS["border_strong"]}; background: #e5e7eb;
            }}
            QCheckBox#RosterFileOrderSwitch::indicator:checked {{
                background: {COLORS["accent"]}; border-color: {COLORS["accent_hover"]};
            }}
            """
        )
        self.file_order_switch.toggled.connect(self._on_file_order_toggled)
        order_row.addWidget(self._file_order_lbl_asc)
        order_row.addWidget(self.file_order_switch)
        order_row.addWidget(self._file_order_lbl_desc)
        order_row.addStretch()
        lay.addLayout(order_row)
        self._update_file_order_labels()

        self.assign_summary_label = h.caption_label("")
        lay.addWidget(self.assign_summary_label)

        self.roster_table = QTableWidget(0, 5)
        self.roster_table.setHorizontalHeaderLabels(["未受験", "組", "番号", "ID", "氏名"])
        for i, w in enumerate([56, 60, 60, 110, 200]):
            self.roster_table.setColumnWidth(i, w)
        self.roster_table.setEditTriggers(QTableWidget.NoEditTriggers)
        make_expanding(self.roster_table)
        self.roster_table.cellClicked.connect(self._on_roster_cell_clicked)
        self.roster_table.setVisible(False)
        lay.addWidget(self.roster_table, 1)
        make_expanding(box)
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
                "外部得点は「ID,氏名,得点」の3列で貼り付けます。"
                "取込前に、IDを基準にして本体採点の氏名と照合します。"
                "総計点 = 記述欄得点の合計 + 外部得点。"
            )
        )
        self.external_edit = QPlainTextEdit()
        self.external_edit.setPlaceholderText("例:\nID,氏名,得点\n1001,山田太郎,45\n1002,佐藤花子,38")
        self.external_edit.setFixedHeight(110)
        lay.addWidget(self.external_edit)
        ext_row = QHBoxLayout()
        ext_row.addWidget(h.button("照合して取込", self._on_import_external, variant="success"))
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
                "ID,氏名,得点\n"
                + "\n".join(
                    f"{e['studentId']},{e.get('name') or ''},{e['score']}" for e in ext
                )
            )
        self._update_roster_import_button()

    def _update_roster_import_button(self) -> None:
        if not getattr(self, "import_roster_btn", None):
            return
        if not self.app.active_test_id:
            self.import_roster_btn.setVisible(True)
            self.import_roster_btn.setEnabled(False)
            return
        st = get_id_assignment_status(self.app.active_test_id)
        skip = bool(st.get("skipAssignment"))
        self.import_roster_btn.setVisible(not skip)
        if not skip:
            self.import_roster_btn.setEnabled(True)

    def _update_assign_status(self) -> None:
        st = get_id_assignment_status(self.app.active_test_id)
        if st["skipAssignment"]:
            msg = (
                f"IDマーク欄から {st['withIdCount']}/{st['resultCount']} 件の ID を取得済みのため、"
                "名簿からの割り当ては不要です。"
            )
        elif st["resultCount"] == 0:
            msg = "採点結果がまだありません。⑦ OCR実行を先に行ってください。"
        else:
            msg = (
                f"回答 {st['resultCount']} 件中 ID 入力済み {st['withIdCount']} 件。"
                "名簿を選択し、未受験者を除外してから割り当ててください"
                "（生徒ID昇順 ↔ ファイル名昇順または降順で 1:1 対応）。"
            )
        self.assign_status_label.setText(msg)
        self._update_roster_import_button()

    def _on_file_order_toggled(self, _checked: bool) -> None:
        self._update_file_order_labels()

    def _update_file_order_labels(self) -> None:
        desc = self.file_order_switch.isChecked()
        self._file_order_lbl_asc.setStyleSheet(
            f"font-weight: {'400' if desc else '700'};"
            f" color: {COLORS['text_muted'] if desc else COLORS['text']};"
        )
        self._file_order_lbl_desc.setStyleSheet(
            f"font-weight: {'700' if desc else '400'};"
            f" color: {COLORS['text'] if desc else COLORS['text_muted']};"
        )

    def _assign_file_order(self) -> str:
        return "desc" if self.file_order_switch.isChecked() else "asc"

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

    def _apply_imported_roster_state(
        self, roster_name: str, absent_students: list[dict[str, str]]
    ) -> None:
        self.roster_combo.blockSignals(True)
        self.roster_combo.setCurrentText(roster_name)
        self.roster_combo.blockSignals(False)
        self._absent_keys = {
            f"id:{a.get('studentId')}" if a.get("studentId") else f"name:{a.get('name')}"
            for a in absent_students
        }
        self._roster_rows = get_roster_rows(roster_name)
        self._render_roster_table()
        self.roster_table.setVisible(True)
        self.assign_btn.setEnabled(True)
        self._update_assign_summary()

    def _on_import_roster_from_test(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        st = get_id_assignment_status(test_id)
        if st.get("skipAssignment"):
            h.info(
                self,
                "不要",
                "IDマーク欄から ID を取得済みのため、名簿・未受験者のインポートは不要です。",
            )
            return
        sources = list_roster_absent_import_sources(test_id)
        if not sources:
            h.warn(
                self,
                "インポート元なし",
                "名簿と未受験者が保存された他テストがありません。",
            )
            return
        dlg = RosterImportFromTestDialog(self, sources)
        if dlg.exec() != QDialog.Accepted or not dlg.selected_test_id:
            return
        try:
            res = import_roster_absent_from_test(test_id, dlg.selected_test_id)
            absent_state = get_roster_absent_state(test_id)
            self._apply_imported_roster_state(
                res["rosterName"],
                list(absent_state.get("absentStudents") or []),
            )
            h.info(
                self,
                "反映完了",
                f"名簿「{res['rosterName']}」と未受験 {res['absentCount']} 名を反映しました。",
            )
        except Exception as e:
            h.error(self, "インポートエラー", str(e))

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
                f"回答 {preview['resultCount']} 件 / 受験予定 {preview['expectedCount']} 名で一致しません。\n"
                "未受験者の指定を確認してください。",
            )
            return
        try:
            res = assign_ids_from_roster(
                self.app.active_test_id,
                name,
                absent,
                file_order=self._assign_file_order(),
            )
            if res.get("skipped"):
                h.info(self, "スキップ", res.get("message", ""))
            else:
                order_label = (
                    "ファイル名降順"
                    if res.get("fileOrder") == "desc"
                    else "ファイル名昇順"
                )
                h.info(
                    self,
                    "割当完了",
                    f"{res['assigned']} 名の ID・氏名を割り当てました（生徒ID昇順 × {order_label}）。",
                )
            self._update_assign_status()
        except Exception as e:
            h.error(self, "割当エラー", str(e))

    def _on_paste_roster(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("名簿 TSV 貼り付け")
        dlg.resize(560, 360)
        h.enable_dialog_maximize(dlg)
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
            h.warn(self, "形式エラー", "「ID,氏名,得点」の3列で入力してください。")
            return
        try:
            compared = compare_external_scores(self.app.active_test_id, rows)
        except Exception as e:
            h.error(self, "照合エラー", str(e))
            return
        dialog = self._external_dialog
        if dialog is None:
            dialog = ExternalScoreMatchDialog(self.app)
            dialog.importRequested.connect(self._on_external_dialog_import)
            self._external_dialog = dialog
        dialog.set_comparison(self.app.active_test_id, rows, compared)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_external_dialog_import(self) -> None:
        dialog = self._external_dialog
        if dialog is None or not dialog.accepted_import:
            return
        try:
            count = import_external_scores(dialog.test_id, dialog.feed_rows)
        except Exception as e:
            dialog.accepted_import = False
            h.error(self, "エラー", str(e))
            return
        dialog.accepted_import = False
        dialog.hide()
        self.score_status_label.setText(f"外部得点 {count} 件を取り込み、総計点を再計算しました。")
        h.info(self, "取込完了", f"外部得点 {count} 件を取り込みました。")
