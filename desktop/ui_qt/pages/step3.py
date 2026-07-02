"""③ テキスト化（OCRバッチ）ページ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import export_results_to_excel, get_result_preview, get_test_info, save_student_folder
from services.batch_processor import run_batch_ocr
from services.work_queue import build_ocr_work_queue
from ui_qt import helpers as h
from ui_qt.helpers import ProgressBridge


class Step3Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("③ テキスト化（OCRバッチ）"))
        root.addWidget(
            h.muted_label("生徒解答フォルダ内の PDF / JPG / PNG を自動補正→OCR→SQLite に一括保存します。")
        )

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("解答フォルダ"))
        self.inbox_edit = QLineEdit()
        folder_row.addWidget(self.inbox_edit, 1)
        folder_row.addWidget(h.button("参照…", self._pick_inbox))
        root.addLayout(folder_row)

        self.queue_stats = h.muted_label("")
        root.addWidget(self.queue_stats)

        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        prog_row.addWidget(self.progress, 1)
        self.progress_label = QLabel("")
        prog_row.addWidget(self.progress_label)
        root.addLayout(prog_row)

        btns = QHBoxLayout()
        self.run_btn = h.button("未処理のみ OCR", self._on_run_ocr, variant="primary")
        btns.addWidget(self.run_btn)
        btns.addWidget(h.button("キュー更新", self.refresh))
        btns.addWidget(h.button("Excel エクスポート", self._on_export_excel))
        btns.addStretch()
        root.addLayout(btns)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

    def _pick_inbox(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "生徒解答フォルダを選択")
        if path and self.app.require_active_test():
            self.inbox_edit.setText(path)
            save_student_folder(self.app.active_test_id, path)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        info = get_test_info(self.app.active_test_id)
        folder = info.get("folderPath") or ""
        self.inbox_edit.setText(folder)
        queue = build_ocr_work_queue(self.app.active_test_id, folder)
        st = queue["stats"]
        self.queue_stats.setText(
            f"未処理: {st['pending']} 件 / OCRのみ: {st['ocrOnly']} / 補正+OCR: {st['warpAndOcr']} / "
            f"反映済: {st['inSheet']} / inbox内: {st['inInbox']}"
        )
        preview = get_result_preview(self.app.active_test_id)
        self.log.clear()
        for row in preview[-30:]:
            texts = ", ".join(f"{k}={v}" for k, v in row["textMapping"].items())
            self.log.appendPlainText(f"{row['fileName']}: {texts}")

    def _on_run_ocr(self) -> None:
        if not self.app.require_active_test():
            return
        folder = self.inbox_edit.text().strip()
        if not folder:
            h.error(self, "エラー", "解答フォルダを指定してください。")
            return

        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log.appendPlainText("OCR バッチを開始…")

        bridge = ProgressBridge(self)
        bridge.updated.connect(self._update_progress)
        test_id = self.app.active_test_id

        def task():
            def on_progress(current: int, total: int, name: str) -> None:
                bridge.updated.emit(current, total, name)

            return run_batch_ocr(test_id, folder, on_progress=on_progress)

        h.run_in_thread(self, task, self._on_ocr_done)

    def _update_progress(self, current: int, total: int, name: str) -> None:
        pct = int(current / total * 100) if total else 0
        self.progress.setValue(pct)
        self.progress_label.setText(f"{current}/{total} {name}")

    def _on_ocr_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self.run_btn.setEnabled(True)
        if err:
            h.error(self, "OCR エラー", str(err))
            self.log.appendPlainText(f"エラー: {err}")
            return
        assert result is not None
        flush = result.get("flush", {})
        self.log.appendPlainText(
            f"完了: 処理 {result.get('processed', 0)} 件 / "
            f"書込 {flush.get('written', 0)} / スキップ {flush.get('skipped', 0)} / "
            f"エラー {len(result.get('errors', []))}"
        )
        for e in result.get("errors", []):
            self.log.appendPlainText(f"  × {e.get('fileName')}: {e.get('error')}")
        self.refresh()
        h.info(self, "OCR 完了", f"書込 {flush.get('written', 0)} 件")

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
