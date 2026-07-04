"""③ テキスト化（OCRバッチ）ページ — 手動確認・選択実行型。"""

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
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import test_warped
from models.test_repo import (
    build_pending_rows_tsv,
    build_results_tsv,
    clear_step3_failed_entry,
    export_results_to_excel,
    get_answer_fields,
    get_test_info,
    import_results_from_excel,
    normalize_file_name,
    reset_step3_data,
    save_student_folder,
    set_step3_failed_entry,
)
from services.batch_processor import STAGE_LABELS, run_batch_ocr
from services.work_queue import build_file_inventory
from ui_qt import helpers as h
from ui_qt.helpers import ProgressBridge
from ui_qt.layout_helpers import CollapsibleSection, main_table_frame, make_expanding
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

# 再認識時に既定でチェックするステータス
_DEFAULT_CHECK = frozenset({"未処理", "補正済", "失敗"})


class Step3Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._fields: list[dict[str, Any]] = []
        self._row_by_name: dict[str, int] = {}
        self._inventory_rows: list[dict[str, Any]] = []
        self._last_pending_rows: list[dict[str, Any]] = []
        self._loaded_test_id: str | None = None
        self._scanned = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("③ テキスト化（OCRバッチ）"))
        root.addWidget(
            h.muted_label(
                "「フォルダを再認識」で一覧を表示し、チェックしたファイルだけ処理します。"
                "①で「IDマーク欄あり」のとき、補正後に生徒IDを OMR で読み取ります（読めない桁は ?）。"
            )
        )

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("解答フォルダ"))
        self.inbox_edit = QLineEdit()
        folder_row.addWidget(self.inbox_edit, 1)
        folder_row.addWidget(h.button("参照…", self._pick_inbox))
        folder_row.addWidget(h.button("フォルダを再認識", self._scan_folder, variant="primary"))
        root.addLayout(folder_row)

        self.queue_stats = h.muted_label("一覧未表示 — 「フォルダを再認識」を押してください。")
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

        # --- 選択・実行 ---
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("チェック:"))
        for label, mode in [
            ("全て", "all"),
            ("全解除", "none"),
            ("＋未処理", "unprocessed"),
            ("＋補正済", "warped"),
            ("＋反映済", "processed"),
            ("＋失敗", "failed"),
        ]:
            tip = "該当ステータスにチェックを入れる" if mode != "none" else "すべてのチェックを外す"
            btn = h.button(label, partial(self._select_by_status, mode))
            btn.setToolTip(tip)
            sel_row.addWidget(btn)
        sel_row.addStretch()
        root.addLayout(sel_row)

        btns = QHBoxLayout()
        self.run_btn = h.button("チェックしたファイルを OCR", self._on_run_ocr, variant="primary")
        btns.addWidget(self.run_btn)
        btns.addWidget(h.button("③をリセット", self._on_reset, variant="danger-soft"))
        btns.addStretch()
        root.addLayout(btns)

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
        hdr.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_COL_FAIL, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(_COL_CHECK, 56)
        self.table.verticalHeader().setDefaultSectionSize(32)
        wire_toggle_columns(self.table, (_COL_CHECK,), lambda _r, _c, _v: self._update_check_count())
        root.addWidget(main_table_frame("ファイル別の処理状況", self.table), 1)

        tsv_body = QFrame()
        tsv_lay = QVBoxLayout(tsv_body)
        tsv_lay.setContentsMargins(0, 0, 0, 0)
        tsv_lay.setSpacing(6)
        tsv_btns = QHBoxLayout()
        tsv_btns.addWidget(h.button("TSVをコピー", self._copy_tsv, variant="success"))
        tsv_btns.addWidget(h.button("TSV再生成", self._refresh_tsv))
        tsv_btns.addWidget(h.button("Excel エクスポート", self._on_export_excel))
        tsv_btns.addWidget(h.button("Excel インポート", self._on_import_excel))
        tsv_btns.addStretch()
        tsv_lay.addLayout(tsv_btns)
        tsv_lay.addWidget(
            h.caption_label(
                "Excel エクスポート／インポートで「ファイル別の処理状況」の一覧を保存・復元できます。"
                "インポート後は一覧を自動更新します。"
            )
        )
        self.tsv_view = QPlainTextEdit()
        self.tsv_view.setReadOnly(True)
        self.tsv_view.setPlaceholderText("「TSV再生成」で DB の採点結果を表示します。")
        self.tsv_view.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; font-size: 11px;"
            f"background: {COLORS['surface']};"
        )
        self.tsv_view.setMinimumHeight(100)
        self.tsv_view.setMaximumHeight(180)
        tsv_lay.addWidget(self.tsv_view)
        root.addWidget(
            CollapsibleSection(
                "採点結果TSV・Excel",
                tsv_body,
                collapsed=True,
                tint="#fffbeb",
            )
        )

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(72)
        self.log.setPlaceholderText("サマリログ")
        root.addWidget(self.log)

    # --- タブ表示時（自動スキャン・OCR はしない）---

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        info = get_test_info(test_id)
        self.inbox_edit.setText(info.get("folderPath") or "")
        self._fields = get_answer_fields(test_id)

        if test_id != self._loaded_test_id:
            self._loaded_test_id = test_id
            self._scanned = False
            self._clear_view()

        if not self._scanned:
            self.status_label.setText(
                "「フォルダを再認識」で解答画像の一覧を表示してから、処理するファイルを選んでください。"
            )

    def _clear_view(self) -> None:
        self.table.setRowCount(0)
        self._row_by_name = {}
        self._inventory_rows = []
        self.queue_stats.setText("一覧未表示 — 「フォルダを再認識」を押してください。")
        self.progress.setValue(0)
        self.progress_label.setText("")
        self.tsv_view.clear()

    def _pick_inbox(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "生徒解答フォルダを選択")
        if path and self.app.require_active_test():
            self.inbox_edit.setText(path)
            save_student_folder(self.app.active_test_id, path)
            self._scanned = False
            self.status_label.setText("フォルダを変更しました。「フォルダを再認識」で一覧を更新してください。")

    def _scan_folder(self) -> None:
        if not self.app.require_active_test():
            return
        folder = self.inbox_edit.text().strip()
        if not folder:
            h.error(self, "エラー", "解答フォルダを指定してください。")
            return

        test_id = self.app.active_test_id
        self._fields = get_answer_fields(test_id)
        inv = build_file_inventory(test_id, folder)
        st = inv["stats"]
        self.queue_stats.setText(
            f"合計 {st['total']} 件 — 未処理 {st['unprocessed']} / 補正済 {st['warped']} / "
            f"反映済 {st['processed']} / 失敗 {st['failed']} / フォルダ内 {st['inInbox']} 件"
        )
        self._inventory_rows = inv["rows"]
        self._rebuild_table(self._inventory_rows)
        self._scanned = True
        n = sum(1 for i in range(self.table.rowCount()) if self._row_checked(i))
        self.status_label.setText(
            f"{st['total']} 件を認識しました（{n} 件を選択中）。"
            "「チェックしたファイルを OCR」を押してください。"
        )
        self.log.appendPlainText(f"--- フォルダ再認識: {st['total']} 件 ---")

    def _rebuild_table(self, rows_data: list[dict[str, Any]]) -> None:
        fields = self._fields
        headers = ["OCR", "状態", "失敗理由", "ファイル名", "生徒ID"]
        headers.extend(f.get("displayName") or f["id"] for f in fields)
        headers.append("DB")

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self._row_by_name = {}
        self.table.setRowCount(len(rows_data))

        for i, rd in enumerate(rows_data):
            key = normalize_file_name(rd["fileName"])
            self._row_by_name[key] = i
            default_check = rd.get("status") in _DEFAULT_CHECK
            self._set_row(i, rd, checked=default_check)

    def _row_checked(self, row_idx: int) -> bool:
        return is_toggle_checked(self.table.item(row_idx, _COL_CHECK))

    def _set_check_cell(self, row_idx: int, checked: bool) -> None:
        item = self.table.item(row_idx, _COL_CHECK)
        if item is not None:
            set_toggle_checked(item, checked)
            return
        self.table.setItem(row_idx, _COL_CHECK, make_toggle_item(checked))

    def _set_row(self, row_idx: int, data: dict[str, Any], *, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._row_checked(row_idx)
        self._set_check_cell(row_idx, checked)

        status = data.get("status") or "未処理"
        fail = data.get("fail") or ""
        file_name = str(data.get("fileName") or "")
        hint = data.get("hint") or ""

        self._set_cell(row_idx, _COL_STATUS, status, self._status_color(status))
        if fail:
            fail_item = QTableWidgetItem(self._truncate(fail, 48))
            fail_item.setToolTip(fail)
            fail_item.setForeground(QColor(COLORS["danger"]))
            self.table.setItem(row_idx, _COL_FAIL, fail_item)
        else:
            self._set_cell(row_idx, _COL_FAIL, "")
        self._set_cell(row_idx, _COL_FILE, file_name + hint)
        self._set_cell(row_idx, _COL_STUDENT, data.get("studentId") or "—")

        texts = data.get("texts") or {}
        for fi, field in enumerate(self._fields):
            col = _COL_FIELD_START + fi
            txt = texts.get(field["id"], "—")
            item = QTableWidgetItem(self._truncate(str(txt), 32))
            item.setToolTip(str(txt))
            self.table.setItem(row_idx, col, item)

        self._set_cell(row_idx, _COL_FIELD_START + len(self._fields), data.get("db") or "—")

    def _set_cell(
        self, row: int, col: int, text: str, color: QColor | None = None
    ) -> None:
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
        n = sum(1 for i in range(self.table.rowCount()) if self._row_checked(i))
        if self._scanned:
            self.status_label.setText(f"{n} 件を選択中")

    def _select_by_status(self, mode: str) -> None:
        if self.table.rowCount() == 0:
            h.warn(self, "一覧なし", "先に「フォルダを再認識」でファイル一覧を表示してください。")
            return
        if mode == "none":
            for i in range(len(self._inventory_rows)):
                self._set_check_cell(i, False)
            self._update_check_count()
            return
        if mode == "all":
            for i in range(len(self._inventory_rows)):
                self._set_check_cell(i, True)
            self._update_check_count()
            return
        # ＋未処理 等: 該当ステータスにだけチェックを入れる（他はそのまま）
        matched = 0
        for i, rd in enumerate(self._inventory_rows):
            status = rd.get("status") or ""
            hit = (
                (mode == "unprocessed" and status == "未処理")
                or (mode == "warped" and status == "補正済")
                or (mode == "processed" and status == "反映済")
                or (mode == "failed" and status == "失敗")
            )
            if not hit:
                continue
            self._set_check_cell(i, True)
            matched += 1
        if matched == 0:
            labels = {
                "unprocessed": "未処理",
                "warped": "補正済",
                "processed": "反映済",
                "failed": "失敗",
            }
            h.warn(self, "該当なし", f"「{labels.get(mode, mode)}」の行がありません。")
        self._update_check_count()

    def _get_checked_queue_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        skipped_processed: list[str] = []
        for i, rd in enumerate(self._inventory_rows):
            if not self._row_checked(i):
                continue
            if rd.get("status") == "反映済":
                skipped_processed.append(rd["fileName"])
                continue
            q = rd.get("queueItem")
            if q:
                items.append(q)
        return items, skipped_processed

    def _row_index(self, file_name: str) -> int | None:
        return self._row_by_name.get(normalize_file_name(file_name))

    def _on_detail_progress(self, ev: dict[str, Any]) -> None:
        file_name = str(ev.get("fileName") or "")
        stage = str(ev.get("stage") or "")
        status = str(ev.get("status") or "")
        index = int(ev.get("index") or 0)
        total = int(ev.get("total") or 0)

        if not file_name and stage in ("save", "archive"):
            label = self._stage_label(stage)
            if status == "processing":
                self.status_label.setText(f"一括{label}中…")
            elif status == "done" and stage == "save":
                self.status_label.setText("DB保存完了 — 原本を退避中…")
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
            self._set_cell(row_idx, _COL_FAIL, "")
        elif status == "failed":
            err = str(ev.get("error") or "")
            detail = f"{stage_label} — {err}" if err else stage_label
            self.status_label.setText(f"{index}/{total}  失敗（{stage_label}）: {file_name}")
            self._set_cell(row_idx, _COL_STATUS, "失敗", QColor(COLORS["danger"]))
            fail_item = QTableWidgetItem(self._truncate(detail, 48))
            fail_item.setToolTip(detail)
            fail_item.setForeground(QColor(COLORS["danger"]))
            self.table.setItem(row_idx, _COL_FAIL, fail_item)
            if self.app.active_test_id:
                set_step3_failed_entry(self.app.active_test_id, file_name, err, stage)
        elif status == "done" and stage == "done":
            result = ev.get("result") or {}
            texts = result.get("textMapping") or {}
            self._set_row(
                row_idx,
                {
                    "fileName": file_name,
                    "status": "完了",
                    "fail": "",
                    "studentId": result.get("studentId") or "",
                    "texts": texts,
                    "db": "未反映",
                    "hint": "",
                },
                checked=None,
            )
            self.status_label.setText(f"{index}/{total}  完了: {file_name}")

    def _on_run_ocr(self) -> None:
        if not self.app.require_active_test():
            return
        if not self._scanned:
            h.warn(self, "一覧未表示", "先に「フォルダを再認識」でファイル一覧を表示してください。")
            return

        folder = self.inbox_edit.text().strip()
        if not folder:
            h.error(self, "エラー", "解答フォルダを指定してください。")
            return

        test_id = self.app.active_test_id
        if not get_answer_fields(test_id):
            h.error(
                self,
                "記述欄未設定",
                "先に ① 回答欄設定で模範解答と記述欄を登録してください。",
            )
            return

        items, skipped_processed = self._get_checked_queue_items()
        if skipped_processed:
            h.warn(
                self,
                "反映済みはスキップ",
                "反映済みのファイルは再 OCR しません（③リセット後に再実行できます）:\n"
                + "\n".join(skipped_processed[:8])
                + (" …" if len(skipped_processed) > 8 else ""),
            )
        if not items:
            h.warn(self, "選択なし", "OCR するファイルにチェックを入れてください。")
            return

        total = len(items)
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText(f"0/{total}")
        self.status_label.setText(f"OCR 開始（チェック {total} 件）…")
        self.log.appendPlainText(f"--- OCR 開始（チェック {total} 件）---")

        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)
        bridge.detailed.connect(self._on_detail_progress)

        def task():
            def on_progress(current: int, t: int, name: str) -> None:
                bridge.updated.emit(current, t, name)

            def on_detail(ev: dict[str, Any]) -> None:
                bridge.detailed.emit(ev)

            return run_batch_ocr(
                test_id,
                folder,
                on_progress=on_progress,
                on_detail=on_detail,
                items=items,
            )

        h.run_in_thread(self, task, self._on_ocr_done)

    def _update_progress(self, current: int, total: int, name: str) -> None:
        pct = int(current / total * 100) if total else 0
        self.progress.setValue(pct)
        self.progress_label.setText(f"{current}/{total}")

    def _on_ocr_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self.run_btn.setEnabled(True)
        test_id = self.app.active_test_id
        if err:
            h.error(self, "OCR エラー", str(err))
            self.log.appendPlainText(f"致命的エラー: {err}")
            self.status_label.setText(f"中断: {err}")
            return
        assert result is not None
        flush = result.get("flush", {})
        processed = int(result.get("processed", 0))
        written = int(flush.get("written", 0))
        err_count = len(result.get("errors", []))
        warped_dir = test_warped(test_id)
        self._last_pending_rows = [
            log["row"]
            for log in result.get("itemLogs", [])
            if log.get("status") == "done" and log.get("row")
        ]

        summary = (
            f"完了: 成功 {processed} 件 / DB書込 {written} / "
            f"スキップ {flush.get('skipped', 0)} / 失敗 {err_count}"
        )
        self.log.appendPlainText(summary)
        if written:
            self.log.appendPlainText(f"補正画像: {warped_dir}")

        for e in result.get("errors", []):
            stage = self._stage_label(str(e.get("stage") or "unknown"))
            fname = str(e.get("fileName") or "")
            msg = f"  × {fname}: [{stage}] {e.get('error')}"
            self.log.appendPlainText(msg)
            if test_id and fname:
                set_step3_failed_entry(test_id, fname, str(e.get("error") or ""), str(e.get("stage") or ""))

        for log in result.get("itemLogs", []):
            if log.get("status") == "done" and test_id:
                clear_step3_failed_entry(test_id, log["fileName"])

        self._scan_folder()

        if processed == 0 and err_count == 0:
            self.status_label.setText("処理対象なし")
        elif err_count:
            self.status_label.setText(f"完了（失敗 {err_count} 件）— 一覧を更新しました")
            h.warn(
                self,
                "一部エラー",
                f"書込 {written} 件 / 失敗 {err_count} 件\n\n"
                "「失敗」選択ボタンで失敗分を再チェックできます。",
            )
        else:
            self.status_label.setText(f"全件完了 — DB書込 {written} 件")
            h.info(self, "OCR 完了", f"書込 {written} 件\n補正画像: {warped_dir}")

    def _on_reset(self) -> None:
        if not self.app.require_active_test():
            return
        ans = QMessageBox.question(
            self,
            "③をリセット",
            "採点結果（OCRテキスト）・補正画像・失敗記録をすべて消去し、\n"
            "「元画像」フォルダの原本を解答フォルダへ戻します。\n\n"
            "この操作は取り消せません。続行しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            res = reset_step3_data(self.app.active_test_id)
            self._scanned = False
            self._clear_view()
            self.status_label.setText("リセット完了 — 「フォルダを再認識」からやり直してください。")
            self.log.appendPlainText(
                f"リセット: 結果削除 {res['deletedResults']} / "
                f"補正削除 {res['deletedWarped']} / 原本復元 {res['restored']}"
            )
            h.info(
                self,
                "リセット完了",
                f"採点結果 {res['deletedResults']} 件削除\n"
                f"補正画像 {res['deletedWarped']} 件削除\n"
                f"原本 {res['restored']} 件を解答フォルダへ復元",
            )
        except Exception as e:
            h.error(self, "リセット失敗", str(e))

    def _refresh_tsv(self) -> None:
        if not self.app.require_active_test():
            self.tsv_view.setPlainText("")
            return
        tsv = build_results_tsv(self.app.active_test_id)
        if not tsv and self._last_pending_rows:
            tsv = build_pending_rows_tsv(self.app.active_test_id, self._last_pending_rows)
        self.tsv_view.setPlainText(tsv)
        if tsv:
            lines = tsv.count("\n") + 1
            self.tsv_view.setToolTip(f"{lines} 行（ヘッダー含む）")

    def _copy_tsv(self) -> None:
        text = self.tsv_view.toPlainText().strip()
        if not text:
            h.warn(self, "コピー不可", "TSV データがありません。「TSV再生成」を押してください。")
            return
        QApplication.clipboard().setText(text)
        h.info(self, "コピー完了", "TSV をクリップボードにコピーしました。")

    def _on_export_excel(self) -> None:
        if not self.app.require_active_test():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "採点結果を Excel にエクスポート", "", "Excel (*.xlsx)"
        )
        if not path:
            return
        try:
            export_results_to_excel(self.app.active_test_id, path)
            h.info(self, "エクスポート完了", f"保存しました:\n{path}")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_import_excel(self) -> None:
        if not self.app.require_active_test():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "採点結果 Excel をインポート", "", "Excel (*.xlsx)"
        )
        if not path:
            return
        test_id = self.app.active_test_id
        try:
            res = import_results_from_excel(test_id, path)
        except Exception as e:
            h.error(self, "インポート失敗", str(e))
            return
        if res["total"] == 0:
            h.warn(self, "インポート", "取り込める行がありませんでした。")
            return
        folder = self.inbox_edit.text().strip()
        if not folder:
            info = get_test_info(test_id)
            folder = (info.get("folderPath") or "").strip()
            if folder:
                self.inbox_edit.setText(folder)
        if folder:
            self._scan_folder()
        else:
            inv = build_file_inventory(test_id, "")
            self._fields = get_answer_fields(test_id)
            self._inventory_rows = inv["rows"]
            st = inv["stats"]
            self.queue_stats.setText(
                f"Excel 取込 {res['total']} 件 — 合計 {st['total']} 件 "
                f"（新規 {res['inserted']} / 更新 {res['updated']}）"
            )
            self._rebuild_table(self._inventory_rows)
            self._scanned = True
            n = sum(1 for i in range(self.table.rowCount()) if self._row_checked(i))
            self.status_label.setText(f"Excel から {res['total']} 件を復元しました（{n} 件を選択中）。")
        self.log.appendPlainText(
            f"--- Excel インポート: 新規 {res['inserted']} / 更新 {res['updated']} "
            f"/ スキップ {res['skipped']} ---"
        )
        self._refresh_tsv()
        h.info(
            self,
            "インポート完了",
            f"新規 {res['inserted']} 件 / 更新 {res['updated']} 件を取り込みました。\n"
            "「ファイル別の処理状況」を更新しました。",
        )
