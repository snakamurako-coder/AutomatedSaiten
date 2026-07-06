"""音声認識結果の確認ダイアログ。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui_qt import helpers as h


class SpeechConfirmResult:
    ACCEPT = 1
    RETRY = 2
    CANCEL = 3


class SpeechConfirmDialog(QDialog):
    """認識テキストを確認してからテキストボックスへ反映する。"""

    def __init__(self, parent: QWidget | None, transcript: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("音声入力の確認")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(h.muted_label("認識した内容を確認してください。"))

        preview = QLabel(str(transcript or "").strip() or "（空）")
        preview.setWordWrap(True)
        preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview.setStyleSheet(
            "padding: 10px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;"
        )
        root.addWidget(preview)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        accept_btn = h.button("これで良い", variant="primary")
        accept_btn.clicked.connect(lambda: self.done(SpeechConfirmResult.ACCEPT))
        retry_btn = h.button("やり直す")
        retry_btn.clicked.connect(lambda: self.done(SpeechConfirmResult.RETRY))
        cancel_btn = h.button("キャンセル", variant="danger")
        cancel_btn.clicked.connect(lambda: self.done(SpeechConfirmResult.CANCEL))
        btn_row.addWidget(accept_btn, 1)
        btn_row.addWidget(retry_btn, 1)
        btn_row.addWidget(cancel_btn, 1)
        root.addLayout(btn_row)

        accept_btn.setDefault(True)
        accept_btn.setFocus()
