"""③ 生徒回答用紙取り込みページ。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config import test_dir
from models.test_repo import copy_files_to_inbox, resolve_student_inbox
from services.image_loader import is_supported_input_path
from ui_qt import helpers as h
from ui_qt.style import COLORS


class StudentSheetDropZone(QFrame):
    """生徒回答用紙のドラッグ＆ドロップ領域（複数ファイル対応）。"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_count = 0
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)
        self._apply_idle_style()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        self.title_label = QLabel("生徒回答用紙")
        self.title_label.setStyleSheet(
            f"border: none; font-weight: 700; color: {COLORS['text']}; font-size: 14px;"
        )
        self.hint_label = QLabel("PDF / JPG / PNG を\n複数ドロップで一括保存")
        self.hint_label.setWordWrap(True)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(
            f"border: none; color: {COLORS['text_secondary']}; font-size: 12px;"
        )
        lay.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.hint_label, 1, Qt.AlignmentFlag.AlignCenter)

    def set_pending_count(self, count: int) -> None:
        self._pending_count = max(0, int(count))
        if self._pending_count:
            self.hint_label.setText(f"作業フォルダに {self._pending_count} 件")
            self._apply_active_style()
        else:
            self.hint_label.setText("PDF / JPG / PNG を\n複数ドロップで一括保存")
            self._apply_idle_style()

    def clear(self) -> None:
        self.set_pending_count(0)

    def _apply_idle_style(self) -> None:
        self.setStyleSheet(
            f"StudentSheetDropZone {{ background: {COLORS['surface']};"
            f" border: 2px dashed {COLORS['border']}; border-radius: 8px; }}"
        )

    def _apply_active_style(self) -> None:
        self.setStyleSheet(
            f"StudentSheetDropZone {{ background: #f0fdf4;"
            f" border: 2px solid #16a34a; border-radius: 8px; }}"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(
                f"StudentSheetDropZone {{ background: #eff6ff;"
                f" border: 2px dashed {COLORS['accent']}; border-radius: 8px; }}"
            )

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        if self._pending_count:
            self._apply_active_style()
        else:
            self._apply_idle_style()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        files = [p for p in paths if p and is_supported_input_path(p)]
        if not files:
            h.warn(self, "ドロップ", "PDF / JPG / PNG ファイルをドロップしてください。")
            if self._pending_count:
                self._apply_active_style()
            else:
                self._apply_idle_style()
            return
        self.files_dropped.emit(files)
        event.acceptProposedAction()


class Step3Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(h.title_label("③ 生徒回答用紙取り込み"))
        root.addWidget(
            h.muted_label(
                "スキャンした生徒回答用紙（PDF / JPG / PNG）を作業フォルダ（inbox）へ保存します。"
                "⑤〜⑦（トリミング・薄字補正・OCR）でこのフォルダ内のファイルを処理します。"
            )
        )

        body = QHBoxLayout()
        body.setSpacing(16)
        self.student_drop = StudentSheetDropZone()
        self.student_drop.files_dropped.connect(self._on_student_files_dropped)
        body.addWidget(self.student_drop, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(
            h.open_folder_button(self._on_open_inbox_folder, text="作業フォルダを開く")
        )
        side.addWidget(
            h.open_folder_button(self._on_open_test_storage_folder, text="テスト保存フォルダを開く")
        )
        side.addWidget(h.button("再読込", self.refresh))
        side.addStretch()
        body.addLayout(side, 0)
        root.addLayout(body, 1)

        self.status_label = h.caption_label("PDF / JPG / PNG をドロップしてください")
        root.addWidget(self.status_label)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        files = [p for p in paths if p and is_supported_input_path(p)]
        if files:
            self._on_student_files_dropped(files)
        event.acceptProposedAction()

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            self.student_drop.clear()
            return
        inbox = Path(resolve_student_inbox(self.app.active_test_id))
        count = 0
        if inbox.is_dir():
            count = sum(
                1
                for p in inbox.iterdir()
                if p.is_file() and is_supported_input_path(p)
            )
        self.student_drop.set_pending_count(count)
        if count:
            self._set_status(f"作業フォルダに {count} 件あり — 追加でドロップできます")
        else:
            self._set_status("PDF / JPG / PNG をドロップしてください")

    def _on_student_files_dropped(self, paths: list[str]) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        self._set_status(f"{len(paths)} 件を作業フォルダへ保存中…")

        def task():
            return copy_files_to_inbox(test_id, paths)

        def done(result, err):
            if err:
                self._set_status("")
                h.error(self, "保存失敗", str(err))
                return
            copied: list[str] = result
            self.student_drop.set_pending_count(len(copied))
            inbox = resolve_student_inbox(test_id)
            self._set_status(f"作業フォルダに {len(copied)} 件保存しました")
            h.info(
                self,
                "保存完了",
                f"テスト専用作業フォルダ（inbox）に {len(copied)} 件保存しました。\n\n"
                f"フォルダ:\n{inbox}",
            )

        h.run_in_thread(self, task, done)

    def _on_open_inbox_folder(self) -> None:
        if not self.app.require_active_test():
            return
        folder = Path(resolve_student_inbox(self.app.active_test_id))
        folder.mkdir(parents=True, exist_ok=True)
        h.open_in_file_manager(folder, parent=self)

    def _on_open_test_storage_folder(self) -> None:
        if not self.app.require_active_test():
            return
        folder = test_dir(self.app.active_test_id)
        folder.mkdir(parents=True, exist_ok=True)
        h.open_in_file_manager(folder, parent=self)
