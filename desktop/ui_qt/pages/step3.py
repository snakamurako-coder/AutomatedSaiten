"""③ テキスト化（OCRバッチ）ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import test_warped
from models.test_repo import (
    build_pending_rows_tsv,
    build_results_tsv,
    export_results_to_excel,
    get_answer_fields,
    get_result_preview,
    get_test_info,
    normalize_file_name,
    save_student_folder,
)
from services.batch_processor import STAGE_LABELS, run_batch_ocr
from services.work_queue import build_ocr_work_queue
from ui_qt import helpers as h
from ui_qt.helpers import ProgressBridge
from ui_qt.style import COLORS

_COL_STATUS = 0
_COL_FAIL = 1
_COL_FILE = 2
_COL_STUDENT = 3
_COL_FIELD_START = 4


class Step3Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._fields: list[dict[str, Any]] = []
        self._row_by_name: dict[str, int] = {}
        self._last_pending_rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("③ テキスト化（OCRバッチ）"))
        root.addWidget(
            h.muted_label(
                "各ファイルを「原画像読込 → 枠検出・補正 → OCR → DB保存 → 原本退避」の順で処理します。"
                "補正画像は warped/ フォルダに保存されます。"
            )
        )

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("解答フォルダ"))
        self.inbox_edit = QLineEdit()
        folder_row.addWidget(self.inbox_edit, 1)
        folder_row.addWidget(h.button("参照…", self._pick_inbox))
        root.addLayout(folder_row)

        self.queue_stats = h.muted_label("")
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

        btns = QHBoxLayout()
        self.run_btn = h.button("未処理のみ OCR", self._on_run_ocr, variant="primary")
        btns.addWidget(self.run_btn)
        btns.addWidget(h.button("キュー更新", self.refresh))
        btns.addWidget(h.button("Excel エクスポート", self._on_export_excel))
        btns.addStretch()
        root.addLayout(btns)

        splitter = QSplitter(Qt.Vertical)

        table_box = QGroupBox("ファイル別の処理状況")
        table_lay = QVBoxLayout(table_box)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table_lay.addWidget(self.table)
        splitter.addWidget(table_box)

        tsv_box = QGroupBox("採点結果 TSV（スプレッドシートに貼り付け可能）")
        tsv_lay = QVBoxLayout(tsv_box)
        tsv_btns = QHBoxLayout()
        tsv_btns.addWidget(h.button("TSVをコピー", self._copy_tsv, variant="success"))
        tsv_btns.addWidget(h.button("TSV再生成", self._refresh_tsv))
        tsv_btns.addStretch()
        tsv_lay.addLayout(tsv_btns)
        self.tsv_view = QPlainTextEdit()
        self.tsv_view.setReadOnly(True)
        self.tsv_view.setPlaceholderText("OCR 完了後、または「TSV再生成」でここに表示されます。")
        self.tsv_view.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; font-size: 11px;"
            f"background: {COLORS['surface']};"
        )
        self.tsv_view.setMinimumHeight(120)
        tsv_lay.addWidget(self.tsv_view)
        splitter.addWidget(tsv_box)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.setPlaceholderText("サマリログ")
        splitter.addWidget(self.log)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 0)
        root.addWidget(splitter, 1)

    def _pick_inbox(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "生徒解答フォルダを選択")
        if path and self.app.require_active_test():
            self.inbox_edit.setText(path)
            save_student_folder(self.app.active_test_id, path)
            self.refresh()

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        info = get_test_info(test_id)
        folder = info.get("folderPath") or ""
        self.inbox_edit.setText(folder)
        self._fields = get_answer_fields(test_id)

        queue = build_ocr_work_queue(test_id, folder)
        st = queue["stats"]
        self.queue_stats.setText(
            f"未処理: {st['pending']} 件（補正+OCR: {st['warpAndOcr']} / OCRのみ: {st['ocrOnly']}）"
            f" / 反映済: {st['inSheet']} 件 / フォルダ内: {st['inInbox']} 件"
        )

        processed_names = {
            normalize_file_name(r["fileName"]) for r in get_result_preview(test_id)
        }
        self._rebuild_table(queue["items"], processed_names)
        self._refresh_tsv()
        self.status_label.setText("待機中 — 「キュー更新」で件数を確認してから OCR を実行してください。")
        self.progress.setValue(0)
        self.progress_label.setText("")

    def _rebuild_table(
        self, queue_items: list[dict[str, Any]], processed_names: set[str]
    ) -> None:
        fields = self._fields
        headers = ["状態", "失敗理由", "ファイル名", "生徒ID"]
        headers.extend(f.get("displayName") or f["id"] for f in fields)
        headers.append("DB")

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self._row_by_name = {}

        rows_data: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in queue_items:
            key = normalize_file_name(item["name"])
            if key in seen:
                continue
            seen.add(key)
            stage_hint = "（補正済）" if item.get("stage") == "ocr_only" else ""
            rows_data.append(
                {
                    "fileName": item["name"],
                    "status": "待機",
                    "fail": "",
                    "studentId": "",
                    "texts": {},
                    "db": "—",
                    "hint": stage_hint,
                }
            )

        for row in get_result_preview(self.app.active_test_id):
            key = normalize_file_name(row["fileName"])
            if key in seen:
                for rd in rows_data:
                    if normalize_file_name(rd["fileName"]) == key:
                        rd["status"] = "反映済"
                        rd["studentId"] = row.get("studentId") or ""
                        rd["texts"] = row.get("textMapping") or {}
                        rd["db"] = "済"
                        rd["fail"] = ""
                continue
            seen.add(key)
            rows_data.append(
                {
                    "fileName": row["fileName"],
                    "status": "反映済",
                    "fail": "",
                    "studentId": row.get("studentId") or "",
                    "texts": row.get("textMapping") or {},
                    "db": "済",
                    "hint": "",
                }
            )

        rows_data.sort(key=lambda r: r["fileName"])
        self.table.setRowCount(len(rows_data))
        for i, rd in enumerate(rows_data):
            key = normalize_file_name(rd["fileName"])
            self._row_by_name[key] = i
            self._set_row(i, rd)

    def _set_row(self, row_idx: int, data: dict[str, Any]) -> None:
        status = data.get("status") or "待機"
        fail = data.get("fail") or ""
        file_name = str(data.get("fileName") or "")
        hint = data.get("hint") or ""

        self._set_cell(row_idx, _COL_STATUS, status, self._status_color(status))
        self._set_cell(row_idx, _COL_FAIL, fail, QColor(COLORS["danger"]) if fail else None)
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
        if status == "処理中":
            return QColor(COLORS["accent"])
        return None

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        return text if len(text) <= max_len else text[: max_len - 1] + "…"

    def _stage_label(self, stage: str) -> str:
        return STAGE_LABELS.get(stage, stage or STAGE_LABELS["unknown"])

    def _ensure_table_row(self, file_name: str) -> int:
        key = normalize_file_name(file_name)
        if key in self._row_by_name:
            return self._row_by_name[key]
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self._row_by_name[key] = row_idx
        self._set_row(
            row_idx,
            {
                "fileName": file_name,
                "status": "待機",
                "fail": "",
                "studentId": "",
                "texts": {},
                "db": "—",
            },
        )
        return row_idx

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

        row_idx = self._ensure_table_row(file_name)
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
            )
            self.status_label.setText(f"{index}/{total}  完了: {file_name}")

    def _on_run_ocr(self) -> None:
        if not self.app.require_active_test():
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

        queue = build_ocr_work_queue(test_id, folder)
        pending = queue["stats"]["pending"]
        if pending == 0:
            h.warn(
                self,
                "処理対象なし",
                "未処理の画像がありません。\n\n"
                f"解答フォルダ: {folder}\n"
                f"反映済み: {queue['stats']['inSheet']} 件\n\n"
                "PDF / JPG / PNG をこのフォルダに置き、「キュー更新」で件数を確認してから実行してください。",
            )
            return

        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText(f"0/{pending}")
        self.status_label.setText(f"OCR バッチ開始（{pending} 件）…")
        self.log.appendPlainText(f"--- OCR 開始（{pending} 件）---")

        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)
        bridge.detailed.connect(self._on_detail_progress)

        def task():
            def on_progress(current: int, total: int, name: str) -> None:
                bridge.updated.emit(current, total, name)

            def on_detail(ev: dict[str, Any]) -> None:
                bridge.detailed.emit(ev)

            return run_batch_ocr(
                test_id, folder, on_progress=on_progress, on_detail=on_detail
            )

        h.run_in_thread(self, task, self._on_ocr_done)

    def _update_progress(self, current: int, total: int, name: str) -> None:
        pct = int(current / total * 100) if total else 0
        self.progress.setValue(pct)
        self.progress_label.setText(f"{current}/{total}")

    def _on_ocr_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self.run_btn.setEnabled(True)
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
        warped_dir = test_warped(self.app.active_test_id)
        self._last_pending_rows = [
            log["row"] for log in result.get("itemLogs", []) if log.get("status") == "done" and log.get("row")
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
            msg = f"  × {e.get('fileName')}: [{stage}] {e.get('error')}"
            self.log.appendPlainText(msg)

        for log in result.get("itemLogs", []):
            if log.get("status") == "done" and log.get("row"):
                key = normalize_file_name(log["fileName"])
                if key in self._row_by_name:
                    row_idx = self._row_by_name[key]
                    self._set_cell(
                        row_idx, _COL_FIELD_START + len(self._fields), "済"
                    )
                    self._set_cell(
                        row_idx, _COL_STATUS, "反映済", QColor(COLORS["success"])
                    )

        self._refresh_tsv()
        self.refresh()

        if processed == 0 and err_count == 0:
            self.status_label.setText("処理対象なし")
            h.warn(self, "処理なし", "処理されたファイルは 0 件でした。")
        elif err_count:
            failed_names = ", ".join(e.get("fileName", "") for e in result.get("errors", []))
            self.status_label.setText(
                f"完了（失敗 {err_count} 件）— 失敗: {self._truncate(failed_names, 60)}"
            )
            h.warn(
                self,
                "一部エラー",
                f"書込 {written} 件 / 失敗 {err_count} 件\n\n"
                "下の表の「失敗理由」列で、どの工程で止まったか確認できます。",
            )
        else:
            self.status_label.setText(f"全件完了 — DB書込 {written} 件")
            h.info(self, "OCR 完了", f"書込 {written} 件\n補正画像: {warped_dir}")

    def _refresh_tsv(self) -> None:
        if not self.app.active_test_id:
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
            h.warn(self, "コピー不可", "TSV データがありません。先に OCR を実行するか「TSV再生成」を押してください。")
            return
        QApplication.clipboard().setText(text)
        h.info(self, "コピー完了", "TSV をクリップボードにコピーしました。スプレッドシートに貼り付けできます。")

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
