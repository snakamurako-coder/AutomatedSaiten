"""⑤トリミング / ⑥薄字補正 / ⑦OCR実行 — 共通パイプライン UI。"""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import load_config, test_dir, test_results_excel_path, test_warped
from models.test_repo import (
    build_pending_rows_tsv,
    build_results_tsv,
    clear_step3_failed_entry,
    clear_step3_faint_entry,
    export_results_to_excel,
    get_answer_fields,
    import_results_from_excel,
    normalize_file_name,
    reset_step5_trim_data,
    reset_step6_faint_data,
    reset_step7_ocr_data,
    resolve_student_inbox,
    set_step3_failed_entry,
)
from services.batch_processor import (
    STAGE_LABELS,
    run_batch_ocr,
    run_batch_warp,
    run_faint_precheck,
)
from services.work_queue import build_file_inventory, find_warped_for_original
from ui_qt import helpers as h
from ui_qt.faint_review_dialog import FaintReviewDialog
from ui_qt.helpers import ProgressBridge
from ui_qt.layout_helpers import CollapsibleSection, main_table_frame
from ui_qt.manual_warp_dialog import ManualWarpDialog
from ui_qt.style import COLORS
from ui_qt.table_cells import (
    is_toggle_checked,
    make_toggle_item,
    set_toggle_checked,
    wire_toggle_columns,
)

_COL_CHECK = 0
_COL_STATUS = 1
_COL_FAIL = 2
_COL_FILE = 3
_COL_STUDENT = 4
_COL_FIELD_START = 5

_DEFAULT_CHECK = frozenset({"未処理", "補正済", "失敗"})

_PHASE_META: dict[int, dict[str, str]] = {
    5: {
        "title": "⑤ トリミング",
        "desc": (
            "「フォルダを再認識」で一覧を表示し、チェックしたファイルを角度補正（自動または手動）します。"
            "以後の処理は warped フォルダの補正画像を使います。"
        ),
        "action_hint": "「チェックしたファイルを自動トリミング」",
    },
    6: {
        "title": "⑥ 薄字補正",
        "desc": (
            "⑤で作成した補正画像に対して薄い字を検査し、必要なら「目視・強調」でコントラスト等を調整します。"
            "OCR は⑦で実行してください。"
        ),
        "action_hint": "「薄い字を検査」または「チェックしたファイルを薄字補正」",
    },
    7: {
        "title": "⑦ OCR実行",
        "desc": (
            "⑥までの補正画像を OCR し、採点結果を DB に保存します。"
            "②で「IDマーク欄あり」のとき、補正画像から生徒IDを OMR で読み取ります。"
        ),
        "action_hint": "「チェックしたファイルを OCR」",
    },
}


class OcrPipelinePage(QWidget):
    """⑤〜⑦ 共通のファイル一覧・進捗 UI。"""

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._phase = 5
        self._fields: list[dict[str, Any]] = []
        self._row_by_name: dict[str, int] = {}
        self._inventory_rows: list[dict[str, Any]] = []
        self._last_pending_rows: list[dict[str, Any]] = []
        self._loaded_test_id: str | None = None
        self._scanned = False
        self._filter_key = "all"
        self._filter_btns: dict[str, QPushButton] = {}
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._title = h.title_label(_PHASE_META[5]["title"])
        root.addWidget(self._title)
        self._desc = h.muted_label(_PHASE_META[5]["desc"])
        root.addWidget(self._desc)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("解答フォルダ（テスト専用）"))
        self.inbox_edit = QLineEdit()
        self.inbox_edit.setReadOnly(True)
        folder_row.addWidget(self.inbox_edit, 1)
        folder_row.addWidget(
            h.open_folder_button(self._on_open_inbox_folder, text="フォルダを開く")
        )
        self.scan_btn = h.button("フォルダを再認識", self._scan_folder, variant="primary")
        folder_row.addWidget(self.scan_btn)
        root.addLayout(folder_row)

        self.queue_stats = h.muted_label("一覧未表示 — ⑤で「フォルダを再認識」を押してください。")
        root.addWidget(self.queue_stats)

        self.status_label = QLabel("待機中")
        self.status_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {COLORS['text']}; padding: 2px 0;"
        )
        root.addWidget(self.status_label)

        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        prog_row.addWidget(self.progress, 1)
        self.progress_label = QLabel("")
        self.progress_label.setMinimumWidth(200)
        prog_row.addWidget(self.progress_label)
        root.addLayout(prog_row)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("チェック:"))
        for label, mode in [
            ("全て", "all"),
            ("全解除", "none"),
            ("＋未処理", "unprocessed"),
            ("＋補正済", "warped"),
            ("＋要確認", "faint"),
            ("＋反映済", "processed"),
            ("＋失敗", "failed"),
        ]:
            btn = h.button(label, partial(self._select_by_status, mode))
            sel_row.addWidget(btn)
        sel_row.addStretch()
        root.addLayout(sel_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("表示:"))
        for label, key in [
            ("全て", "all"),
            ("未処理", "unprocessed"),
            ("補正済", "warped"),
            ("薄字該当", "faint"),
            ("反映済", "processed"),
            ("失敗", "failed"),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.clicked.connect(partial(self._set_filter, key))
            self._filter_btns[key] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        action_row = QHBoxLayout()
        self.trim_btn = h.button(
            "チェックしたファイルを自動トリミング",
            self._on_run_trim,
            variant="primary",
        )
        action_row.addWidget(self.trim_btn)
        self.manual_warp_btn = h.button(
            "連続手動補正", self._on_continuous_manual_warp, variant="primary"
        )
        action_row.addWidget(self.manual_warp_btn)
        self.faint_precheck_btn = h.button("薄い字を検査", self._on_faint_precheck)
        action_row.addWidget(self.faint_precheck_btn)
        self.faint_enhance_btn = h.button(
            "チェックしたファイルを薄字補正", self._on_checked_faint_enhance
        )
        action_row.addWidget(self.faint_enhance_btn)
        self.ocr_btn = h.button("チェックしたファイルを OCR", self._on_run_ocr, variant="primary")
        action_row.addWidget(self.ocr_btn)
        self.reset_btn = h.button("⑤をリセット", self._on_reset, variant="danger-soft")
        action_row.addWidget(self.reset_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_CHECK, QHeaderView.Fixed)
        hdr.setSectionResizeMode(_COL_FILE, QHeaderView.Stretch)
        self.table.setColumnWidth(_COL_CHECK, 56)
        wire_toggle_columns(self.table, (_COL_CHECK,), lambda _r, _c, _v: self._update_check_count())
        root.addWidget(main_table_frame("ファイル別の処理状況", self.table), 1)

        tsv_body = QFrame()
        tsv_lay = QVBoxLayout(tsv_body)
        tsv_lay.setContentsMargins(0, 0, 0, 0)
        tsv_btns = QHBoxLayout()
        tsv_btns.addWidget(h.button("TSVをコピー", self._copy_tsv, variant="success"))
        tsv_btns.addWidget(h.button("TSV再生成", self._refresh_tsv))
        tsv_btns.addWidget(h.button("Excel エクスポート", self._on_export_excel))
        tsv_btns.addWidget(h.button("Excel インポート", self._on_import_excel))
        tsv_btns.addWidget(
            h.open_folder_button(self._on_open_excel_folder, text="出力フォルダを開く")
        )
        tsv_btns.addStretch()
        tsv_lay.addLayout(tsv_btns)
        tsv_lay.addWidget(
            h.caption_label(
                "Excel エクスポートの既定保存先は、⑭ 個票フォルダと同じテスト配下"
                "（採点結果.xlsx）です。"
            )
        )
        self.tsv_view = QPlainTextEdit()
        self.tsv_view.setReadOnly(True)
        self.tsv_view.setMaximumHeight(180)
        tsv_lay.addWidget(self.tsv_view)
        self.tsv_section = CollapsibleSection(
            "採点結果TSV・Excel", tsv_body, collapsed=True, tint="#fffbeb"
        )
        root.addWidget(self.tsv_section)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(72)
        root.addWidget(self.log)

        self._apply_phase_ui()

    def set_phase(self, step_id: int) -> None:
        if step_id not in _PHASE_META:
            return
        self._phase = step_id
        self._apply_phase_ui()
        if self.app.active_test_id:
            self.refresh()

    def _apply_phase_ui(self) -> None:
        meta = _PHASE_META[self._phase]
        self._title.setText(meta["title"])
        self._desc.setText(meta["desc"])
        self.scan_btn.setVisible(self._phase == 5)
        self.trim_btn.setVisible(self._phase == 5)
        self.manual_warp_btn.setVisible(self._phase == 5)
        self.faint_precheck_btn.setVisible(self._phase == 6)
        self.faint_enhance_btn.setVisible(self._phase == 6)
        self.ocr_btn.setVisible(self._phase == 7)
        self.tsv_section.setVisible(self._phase == 7)
        reset_labels = {5: "⑤をリセット", 6: "⑥をリセット", 7: "⑦をリセット"}
        self.reset_btn.setText(reset_labels[self._phase])
        for i, rd in enumerate(self._inventory_rows):
            if i < self.table.rowCount():
                self._set_action_buttons(i, rd)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        self.inbox_edit.setText(resolve_student_inbox(test_id))
        self._fields = get_answer_fields(test_id)
        if test_id != self._loaded_test_id:
            self._loaded_test_id = test_id
            self._scanned = False
            self._clear_view()
        if not self._scanned:
            hint = (
                "「フォルダを再認識」で解答画像の一覧を表示してから、処理するファイルを選んでください。"
                if self._phase == 5
                else "⑤トリミングで「フォルダを再認識」を実行してから、このステップで処理してください。"
            )
            self.status_label.setText(hint)

    def _clear_view(self) -> None:
        self.table.setRowCount(0)
        self._row_by_name = {}
        self._inventory_rows = []
        self.queue_stats.setText("一覧未表示 — ⑤で「フォルダを再認識」を押してください。")
        self.progress.setValue(0)
        self.progress_label.setText("")
        self.tsv_view.clear()

    def _require_scanned(self) -> bool:
        if self._scanned:
            return True
        if self._phase == 5:
            h.warn(self, "一覧未表示", "先に「フォルダを再認識」でファイル一覧を表示してください。")
        else:
            h.warn(
                self,
                "一覧未表示",
                "⑤トリミングで「フォルダを再認識」を実行してから、このステップで処理してください。",
            )
        return False

    def _inbox_path(self) -> str:
        if not self.app.active_test_id:
            return ""
        return resolve_student_inbox(self.app.active_test_id)

    def _on_open_inbox_folder(self) -> None:
        if self.app.require_active_test():
            h.open_in_file_manager(self._inbox_path(), parent=self)

    def _scan_folder(self) -> None:
        if not self.app.require_active_test():
            return
        folder = self._inbox_path()
        if not folder:
            h.error(self, "エラー", "解答フォルダを指定してください。")
            return
        test_id = self.app.active_test_id
        self._fields = get_answer_fields(test_id)
        inv = build_file_inventory(test_id, folder)
        st = inv["stats"]
        self.queue_stats.setText(
            f"合計 {st['total']} 件 — 未処理 {st['unprocessed']} / 補正済 {st['warped']} / "
            f"要確認 {st.get('faint', 0)} / 反映済 {st['processed']} / 失敗 {st['failed']}"
        )
        self._inventory_rows = inv["rows"]
        self._rebuild_table(self._inventory_rows)
        self._scanned = True
        meta = _PHASE_META[self._phase]
        n = sum(1 for i in range(self.table.rowCount()) if self._row_checked(i))
        self.status_label.setText(
            f"{st['total']} 件を認識しました（{n} 件を選択中）。{meta['action_hint']} を実行できます。"
        )
        self.log.appendPlainText(f"--- フォルダ再認識: {st['total']} 件 ---")

    def _rebuild_table(self, rows_data: list[dict[str, Any]]) -> None:
        headers = ["選択", "状態", "失敗理由", "ファイル名", "生徒ID"]
        headers.extend(f.get("displayName") or f["id"] for f in self._fields)
        headers.extend(["DB", "操作"])
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self._row_by_name = {}
        self.table.setRowCount(len(rows_data))
        for i, rd in enumerate(rows_data):
            self._row_by_name[normalize_file_name(rd["fileName"])] = i
            self._set_row(i, rd, checked=rd.get("status") in _DEFAULT_CHECK)
        self._apply_row_filter()

    def _row_matches_filter(self, rd: dict[str, Any]) -> bool:
        key = self._filter_key
        status = rd.get("status") or ""
        if key == "all":
            return True
        if key == "unprocessed":
            return status == "未処理"
        if key == "warped":
            return status == "補正済"
        if key == "faint":
            return status == "要確認（薄い）" or bool(rd.get("faint"))
        if key == "processed":
            return status == "反映済"
        if key == "failed":
            return status == "失敗"
        return True

    def _apply_row_filter(self) -> None:
        for i, rd in enumerate(self._inventory_rows):
            if i < self.table.rowCount():
                self.table.setRowHidden(i, not self._row_matches_filter(rd))

    def _set_filter(self, key: str) -> None:
        self._filter_key = key
        for k, btn in self._filter_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == key)
            btn.blockSignals(False)
        self._apply_row_filter()

    def _row_checked(self, row_idx: int) -> bool:
        return is_toggle_checked(self.table.item(row_idx, _COL_CHECK))

    def _set_check_cell(self, row_idx: int, checked: bool) -> None:
        item = self.table.item(row_idx, _COL_CHECK)
        if item is not None:
            set_toggle_checked(item, checked)
        else:
            self.table.setItem(row_idx, _COL_CHECK, make_toggle_item(checked))

    def _set_row(self, row_idx: int, data: dict[str, Any], *, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._row_checked(row_idx)
        self._set_check_cell(row_idx, checked)
        status = data.get("status") or "未処理"
        fail = data.get("fail") or ""
        file_name = str(data.get("fileName") or "")
        self._set_cell(row_idx, _COL_STATUS, status, self._status_color(status))
        if fail:
            fail_item = QTableWidgetItem(self._truncate(fail, 48))
            fail_item.setToolTip(fail)
            fail_item.setForeground(
                QColor("#d97706") if status == "要確認（薄い）" else QColor(COLORS["danger"])
            )
            self.table.setItem(row_idx, _COL_FAIL, fail_item)
        else:
            self._set_cell(row_idx, _COL_FAIL, "")
        self._set_cell(row_idx, _COL_FILE, file_name + (data.get("hint") or ""))
        self._set_cell(row_idx, _COL_STUDENT, data.get("studentId") or "—")
        for fi, field in enumerate(self._fields):
            col = _COL_FIELD_START + fi
            txt = (data.get("texts") or {}).get(field["id"], "—")
            item = QTableWidgetItem(self._truncate(str(txt), 32))
            item.setToolTip(str(txt))
            self.table.setItem(row_idx, col, item)
        self._set_cell(row_idx, _COL_FIELD_START + len(self._fields), data.get("db") or "—")
        self._set_action_buttons(row_idx, data)

    def _col_action(self) -> int:
        return _COL_FIELD_START + len(self._fields) + 1

    def _set_action_buttons(self, row_idx: int, data: dict[str, Any]) -> None:
        col = self._col_action()
        if self._phase == 7:
            self.table.removeCellWidget(row_idx, col)
            self._set_cell(row_idx, col, "—")
            return
        q = data.get("queueItem")
        has_path = bool(q and q.get("path"))
        has_warped = bool(str(data.get("warpedPath") or "").strip())
        is_faint = data.get("status") == "要確認（薄い）" or bool(data.get("faint"))
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(2, 0, 2, 0)
        link_style = f"color: {COLORS['accent']}; text-decoration: underline; border: none; background: transparent;"
        warn_style = "color: #d97706; text-decoration: underline; border: none; background: transparent;"
        if self._phase == 6 and (is_faint or has_warped):
            btn = QPushButton("目視・強調")
            btn.setStyleSheet(warn_style if is_faint else link_style)
            btn.clicked.connect(partial(self._open_faint_review_for_row, row_idx))
            lay.addWidget(btn)
        if self._phase == 5 and has_path:
            btn = QPushButton("手動補正")
            btn.setStyleSheet(link_style)
            btn.clicked.connect(partial(self._open_manual_warp_for_row, row_idx))
            lay.addWidget(btn)
        if lay.count() == 0:
            self.table.removeCellWidget(row_idx, col)
            self._set_cell(row_idx, col, "—")
            return
        lay.addStretch()
        self.table.setCellWidget(row_idx, col, host)

    def _set_cell(self, row: int, col: int, text: str, color: QColor | None = None) -> None:
        item = QTableWidgetItem(text)
        if color:
            item.setForeground(color)
        self.table.setItem(row, col, item)

    @staticmethod
    def _status_color(status: str) -> QColor | None:
        if status in ("完了", "反映済"):
            return QColor(COLORS["success"])
        if status == "失敗":
            return QColor(COLORS["danger"])
        if status == "要確認（薄い）":
            return QColor("#d97706")
        if status in ("処理中", "原画像読込", "枠検出・補正", "OCRテキスト化"):
            return QColor(COLORS["accent"])
        if status == "補正済":
            return QColor("#7c3aed")
        return None

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        return text if len(text) <= max_len else text[: max_len - 1] + "…"

    def _stage_label(self, stage: str) -> str:
        return STAGE_LABELS.get(stage, stage or STAGE_LABELS["unknown"])

    def _update_check_count(self) -> None:
        if self._scanned:
            n = sum(1 for i in range(self.table.rowCount()) if self._row_checked(i))
            self.status_label.setText(f"{n} 件を選択中")

    def _select_by_status(self, mode: str) -> None:
        if not self._require_scanned():
            return
        if mode == "none":
            for i in range(len(self._inventory_rows)):
                self._set_check_cell(i, False)
        elif mode == "all":
            for i in range(len(self._inventory_rows)):
                self._set_check_cell(i, True)
        else:
            for i, rd in enumerate(self._inventory_rows):
                status = rd.get("status") or ""
                hit = (
                    (mode == "unprocessed" and status == "未処理")
                    or (mode == "warped" and status == "補正済")
                    or (mode == "faint" and status == "要確認（薄い）")
                    or (mode == "processed" and status == "反映済")
                    or (mode == "failed" and status == "失敗")
                )
                if hit:
                    self._set_check_cell(i, True)
        self._update_check_count()

    def _row_to_work_item(self, rd: dict[str, Any]) -> dict[str, Any] | None:
        """一覧行からバッチ処理用 item を組み立てる。"""
        q = rd.get("queueItem")
        path = str((q or {}).get("path") or rd.get("sourcePath") or "").strip()
        if q:
            item = dict(q)
            if path and not item.get("path"):
                item["path"] = path
                item["id"] = item.get("id") or path
        elif path:
            item = {
                "id": path,
                "name": str(rd.get("fileName") or ""),
                "path": path,
                "mimeType": "image/jpeg",
                "isPdf": False,
                "stage": "warp_and_ocr",
                "warpedPath": "",
                "inArchive": bool(rd.get("inArchive")),
            }
        else:
            return None
        warped = str(rd.get("warpedPath") or "").strip()
        if warped:
            item["warpedPath"] = warped
        return item

    def _get_checked_items(
        self,
        *,
        skip_processed: bool = True,
        require_path: bool = True,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        items: list[dict[str, Any]] = []
        skipped_processed: list[str] = []
        for i, rd in enumerate(self._inventory_rows):
            if not self._row_checked(i):
                continue
            if skip_processed and rd.get("status") == "反映済":
                skipped_processed.append(str(rd.get("fileName") or ""))
                continue
            item = self._row_to_work_item(rd)
            if item is None:
                if require_path:
                    continue
                item = {
                    "name": str(rd.get("fileName") or ""),
                    "path": "",
                    "warpedPath": str(rd.get("warpedPath") or ""),
                }
            items.append(item)
        return items, skipped_processed

    def _get_checked_manual_warp_queue(self) -> list[dict[str, Any]]:
        """チェック行のうち原画像パスがあるものをファイル名順に返す。"""
        by_name: dict[str, dict[str, Any]] = {}
        for i, rd in enumerate(self._inventory_rows):
            if not self._row_checked(i):
                continue
            item = self._row_to_work_item(rd)
            if not item or not str(item.get("path") or "").strip():
                continue
            by_name[str(item.get("name") or rd.get("fileName") or "")] = item
        return sorted(by_name.values(), key=lambda x: str(x.get("name") or ""))

    def _count_checked(self) -> int:
        return sum(1 for i in range(len(self._inventory_rows)) if self._row_checked(i))

    def _row_index(self, file_name: str) -> int | None:
        return self._row_by_name.get(normalize_file_name(file_name))

    def _set_primary_enabled(self, enabled: bool) -> None:
        for btn in (self.trim_btn, self.faint_precheck_btn, self.ocr_btn):
            btn.setEnabled(enabled)

    def _on_detail_progress(self, ev: dict[str, Any]) -> None:
        file_name = str(ev.get("fileName") or "")
        stage = str(ev.get("stage") or "")
        status = str(ev.get("status") or "")
        index = int(ev.get("index") or 0)
        total = int(ev.get("total") or 0)
        if not file_name and stage in ("save", "archive"):
            return
        if not file_name:
            return
        row_idx = self._row_index(file_name)
        if row_idx is None:
            return
        stage_label = self._stage_label(stage)
        if status == "processing":
            self.status_label.setText(f"{index}/{total}  {stage_label}中: {file_name}")
            self._set_cell(row_idx, _COL_STATUS, stage_label, QColor(COLORS["accent"]))
        elif status == "failed":
            err = str(ev.get("error") or "")
            if self.app.active_test_id:
                set_step3_failed_entry(self.app.active_test_id, file_name, err, stage)
            self._set_cell(row_idx, _COL_STATUS, "失敗", QColor(COLORS["danger"]))
        elif status == "done" and stage == "done":
            result = ev.get("result") or {}
            if result.get("textMapping") is not None:
                self._set_row(
                    row_idx,
                    {
                        "fileName": file_name,
                        "status": "完了",
                        "studentId": result.get("studentId") or "",
                        "texts": result.get("textMapping") or {},
                        "db": "未反映",
                    },
                )

    def _update_progress(self, current: int, total: int, name: str) -> None:
        self.progress.setValue(int(current / total * 100) if total else 0)
        self.progress_label.setText(f"{current}/{total}")

    def _on_run_trim(self) -> None:
        if not self.app.require_active_test() or not self._require_scanned():
            return
        if not get_answer_fields(self.app.active_test_id):
            h.error(self, "記述欄未設定", "先に ② 回答欄設定で記述欄を登録してください。")
            return
        if self._count_checked() == 0:
            h.warn(self, "選択なし", "トリミングするファイルにチェックを入れてください。")
            return
        items, skipped = self._get_checked_items()
        if skipped:
            h.warn(self, "反映済みはスキップ", "反映済みのファイルは再トリミングしません。")
        if not items:
            h.warn(
                self,
                "選択なし",
                "チェックした行に原画像パスがありません。解答フォルダ内のファイルを選んでください。",
            )
            return
        test_id = self.app.active_test_id
        total = len(items)
        self._set_primary_enabled(False)
        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)
        bridge.detailed.connect(self._on_detail_progress)

        def task():
            return run_batch_warp(
                test_id,
                items,
                on_progress=lambda c, t, n: bridge.updated.emit(c, t, n),
                on_detail=lambda ev: bridge.detailed.emit(ev),
            )

        h.run_in_thread(self, task, self._on_trim_done)

    def _on_trim_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self._set_primary_enabled(True)
        if err:
            h.error(self, "トリミングエラー", str(err))
            return
        assert result is not None
        test_id = self.app.active_test_id
        for e in result.get("errors") or []:
            if test_id:
                set_step3_failed_entry(
                    test_id, str(e.get("fileName") or ""), str(e.get("error") or ""), "warp"
                )
        for log in result.get("itemLogs") or []:
            if log.get("status") == "done" and test_id:
                clear_step3_failed_entry(test_id, log["fileName"])
        self._scan_folder()
        self.log.appendPlainText(f"--- 自動トリミング完了: {result.get('processed', 0)} 件 ---")

    def _on_run_ocr(self) -> None:
        if not self.app.require_active_test() or not self._require_scanned():
            return
        test_id = self.app.active_test_id
        if not get_answer_fields(test_id):
            h.error(self, "記述欄未設定", "先に ② 回答欄設定で記述欄を登録してください。")
            return
        if self._count_checked() == 0:
            h.warn(self, "選択なし", "OCR するファイルにチェックを入れてください。")
            return
        items, skipped = self._get_checked_items()
        if skipped:
            h.warn(
                self,
                "反映済みはスキップ",
                "反映済みのファイルは再 OCR しません（⑦をリセット後に再実行できます）。",
            )
        if not items:
            h.warn(
                self,
                "選択なし",
                "チェックした行に処理できるファイルがありません（反映済みは対象外です）。",
            )
            return
        no_warp = [
            i["name"]
            for i in items
            if not str(i.get("warpedPath") or "").strip()
            and not find_warped_for_original(test_id, i["name"])
        ]
        if no_warp:
            h.warn(
                self,
                "補正画像なし",
                "⑤トリミングで先に補正してください:\n" + "\n".join(no_warp[:5]),
            )
            return
        total = len(items)
        self._set_primary_enabled(False)
        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)
        bridge.detailed.connect(self._on_detail_progress)

        def task():
            return run_batch_ocr(
                test_id,
                self._inbox_path(),
                on_progress=lambda c, t, n: bridge.updated.emit(c, t, n),
                on_detail=lambda ev: bridge.detailed.emit(ev),
                items=items,
                warp_policy="never",
            )

        h.run_in_thread(self, task, self._on_ocr_done)

    def _on_ocr_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self._set_primary_enabled(True)
        test_id = self.app.active_test_id
        if err:
            h.error(self, "OCR エラー", str(err))
            return
        assert result is not None
        flush = result.get("flush", {})
        for e in result.get("errors") or []:
            if test_id:
                set_step3_failed_entry(
                    test_id, str(e.get("fileName") or ""), str(e.get("error") or ""), str(e.get("stage") or "")
                )
        for log in result.get("itemLogs") or []:
            if log.get("status") == "done" and test_id:
                clear_step3_failed_entry(test_id, log["fileName"])
                clear_step3_faint_entry(test_id, log["fileName"])
        self._scan_folder()
        written = int(flush.get("written", 0))
        self.log.appendPlainText(f"--- OCR 完了: DB書込 {written} 件 ---")
        if written:
            h.info(self, "OCR 完了", f"書込 {written} 件")

    def _on_reset(self) -> None:
        if not self.app.require_active_test():
            return
        dialogs = {
            5: (
                "⑤をリセット",
                "補正画像・失敗／薄い字の記録・OCR結果を消去し、\n"
                "「元画像」フォルダの原本を解答フォルダへ戻します。\n\n"
                "①〜④の内容は保持されます。続行しますか？",
                reset_step5_trim_data,
            ),
            6: (
                "⑥をリセット",
                "薄い字の記録と強調補正（_原.jpg からの巻き戻し）を消去します。\n"
                "⑤の角度補正画像と⑦の OCR 結果は保持されます。続行しますか？",
                reset_step6_faint_data,
            ),
            7: (
                "⑦をリセット",
                "OCR 結果（採点結果 DB）のみ削除します。\n"
                "⑤⑥の補正画像・薄字記録は保持されます。続行しますか？",
                reset_step7_ocr_data,
            ),
        }
        title, msg, fn = dialogs[self._phase]
        if QMessageBox.question(self, title, msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            res = fn(self.app.active_test_id)
            if self._phase == 5:
                self._scanned = False
                self._clear_view()
            else:
                self._scan_folder()
            self.log.appendPlainText(f"--- {title}: {res} ---")
            h.info(self, "リセット完了", str(res))
        except Exception as e:
            h.error(self, "リセット失敗", str(e))

    def _refresh_tsv(self) -> None:
        if not self.app.require_active_test():
            return
        tsv = build_results_tsv(self.app.active_test_id)
        if not tsv and self._last_pending_rows:
            tsv = build_pending_rows_tsv(self.app.active_test_id, self._last_pending_rows)
        self.tsv_view.setPlainText(tsv)

    def _copy_tsv(self) -> None:
        text = self.tsv_view.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _on_export_excel(self) -> None:
        if not self.app.require_active_test():
            return
        path = str(test_results_excel_path(self.app.active_test_id))
        test_results_excel_path(self.app.active_test_id).parent.mkdir(parents=True, exist_ok=True)
        export_results_to_excel(self.app.active_test_id, path)
        h.info(self, "エクスポート完了", f"保存しました:\n{path}")

    def _on_open_excel_folder(self) -> None:
        if self.app.require_active_test():
            h.open_in_file_manager(test_dir(self.app.active_test_id), parent=self)

    def _on_import_excel(self) -> None:
        if not self.app.require_active_test():
            return
        path, _ = QFileDialog.getOpenFileName(self, "採点結果 Excel をインポート", "", "Excel (*.xlsx)")
        if not path:
            return
        res = import_results_from_excel(self.app.active_test_id, path)
        self._scan_folder()
        h.info(self, "インポート完了", f"新規 {res['inserted']} / 更新 {res['updated']} 件")

    def _on_faint_precheck(self) -> None:
        if not self.app.require_active_test() or not self._require_scanned():
            return
        if self._count_checked() == 0:
            h.warn(self, "選択なし", "薄字検査するファイルにチェックを入れてください。")
            return
        test_id = self.app.active_test_id
        items, skipped = self._get_checked_items()
        if skipped:
            h.warn(self, "反映済みはスキップ", "反映済みのファイルは薄字検査しません。")
        if not items:
            h.warn(self, "選択なし", "検査できるチェック行がありません（原画像または補正画像が必要です）。")
            return
        self._set_primary_enabled(False)
        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)

        def task():
            return run_faint_precheck(
                test_id, items, on_progress=lambda c, t, n: bridge.updated.emit(c, t, n)
            )

        h.run_in_thread(self, task, self._on_faint_precheck_done)

    def _on_faint_precheck_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self._set_primary_enabled(True)
        if err:
            h.error(self, "薄い字検査エラー", str(err))
            return
        assert result is not None
        self._scan_folder()
        self.log.appendPlainText(
            f"--- 薄い字検査: 要確認 {result.get('faint', 0)} / OK {result.get('ok', 0)} ---"
        )

    def _review_entry_from_row(self, rd: dict[str, Any]) -> dict[str, Any]:
        q = rd.get("queueItem") or {}
        faint = rd.get("faint") or {}
        return {
            "fileName": str(rd.get("fileName") or ""),
            "reason": str(rd.get("fail") or faint.get("reason") or ""),
            "fieldId": str(faint.get("fieldId") or ""),
            "metrics": dict(faint.get("metrics") or {}),
            "warpedPath": str(rd.get("warpedPath") or faint.get("warpedPath") or q.get("warpedPath") or ""),
            "sourcePath": str(q.get("path") or rd.get("sourcePath") or ""),
            "path": str(q.get("path") or rd.get("sourcePath") or ""),
            "faint": faint,
        }

    def _open_faint_review_queue(self, rows: list[dict[str, Any]]) -> None:
        queue = [self._review_entry_from_row(rd) for rd in rows if rd.get("warpedPath") or (rd.get("queueItem") or {}).get("path")]
        if not queue:
            h.warn(self, "対象なし", "補正画像がある行がありません。")
            return
        dlg = FaintReviewDialog(
            self,
            test_id=self.app.active_test_id,
            queue=queue,
            fields=self._fields,
            selected_file_names={
                normalize_file_name(rd.get("fileName") or "")
                for i, rd in enumerate(self._inventory_rows)
                if self._row_checked(i)
            },
        )
        dlg.exec()
        if self._scanned and dlg.did_bulk_save():
            self._scan_folder()

    def _on_checked_faint_enhance(self) -> None:
        if not self.app.require_active_test() or not self._require_scanned():
            return
        rows = [
            rd
            for i, rd in enumerate(self._inventory_rows)
            if self._row_checked(i) and (str(rd.get("warpedPath") or "").strip() or (rd.get("queueItem") or {}).get("path"))
        ]
        if not rows:
            h.warn(self, "選択なし", "薄字補正するファイルにチェックを入れてください。")
            return
        self._open_faint_review_queue(rows)

    def _open_faint_review_for_row(self, row_idx: int) -> None:
        if 0 <= row_idx < len(self._inventory_rows):
            self._open_faint_review_queue([self._inventory_rows[row_idx]])

    def _warp_dialog_settings(self) -> tuple[str, int]:
        cfg = load_config()
        return cfg.get("default_orientation", "landscape"), 128

    def _on_manual_warp_saved(self, _entry: dict[str, Any] | None = None) -> None:
        if self._scanned:
            self._scan_folder()

    def _open_manual_warp_for_row(self, row_idx: int) -> None:
        if not self.app.require_active_test():
            return
        q = (self._inventory_rows[row_idx].get("queueItem") or {}) if row_idx < len(self._inventory_rows) else {}
        if not q.get("path"):
            h.warn(self, "手動補正不可", "原画像パスがありません。")
            return
        orientation, thresh = self._warp_dialog_settings()
        dlg = ManualWarpDialog(
            self,
            test_id=self.app.active_test_id,
            orientation=orientation,
            on_saved=lambda e: self._on_manual_warp_saved(e),
        )
        dlg.open_single(q, thresh=thresh)
        dlg.exec()

    def _on_continuous_manual_warp(self) -> None:
        if not self.app.require_active_test() or not self._require_scanned():
            return
        if self._count_checked() == 0:
            h.warn(self, "選択なし", "手動補正するファイルにチェックを入れてください。")
            return
        queue = self._get_checked_manual_warp_queue()
        if not queue:
            h.warn(
                self,
                "対象なし",
                "チェックした行に原画像パスがありません。\n"
                "解答フォルダまたは元画像フォルダにファイルがある行を選んでください。",
            )
            return
        orientation, thresh = self._warp_dialog_settings()
        dlg = ManualWarpDialog(
            self,
            test_id=self.app.active_test_id,
            orientation=orientation,
            on_saved=lambda e: self._on_manual_warp_saved(e),
        )
        dlg.open_continuous(queue, thresh=thresh)
        dlg.exec()
