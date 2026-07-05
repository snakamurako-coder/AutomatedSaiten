"""QWebChannel ブリッジ（JavaScript ↔ Python）。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class SpeechRecognitionBridge(QObject):
    """Web Speech API から認識テキストを受け取る。"""

    ready = Signal()
    final_text = Signal(str)
    error = Signal(str)
    ended = Signal()

    @Slot()
    def onReady(self) -> None:
        self.ready.emit()

    @Slot(str)
    def onFinalText(self, text: str) -> None:
        self.final_text.emit(str(text or ""))

    @Slot(str)
    def onError(self, message: str) -> None:
        self.error.emit(str(message or "unknown"))

    @Slot()
    def onEnded(self) -> None:
        self.ended.emit()
