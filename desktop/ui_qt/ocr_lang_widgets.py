"""記述欄 OCR 言語（英語 / 日本語）のトグル UI。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from ui_qt.style import set_variant


class OcrLangToggle(QWidget):
    """英語・日本語の排他トグル（内部値は en / ja）。"""

    def __init__(
        self,
        lang: str = "en",
        on_change: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._updating = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btn_en = QPushButton("英語")
        self._btn_ja = QPushButton("日本語")
        for btn in (self._btn_en, self._btn_ja):
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setFixedWidth(46)
            set_variant(btn, "nav")
            self._group.addButton(btn)
            lay.addWidget(btn)

        self._btn_en.clicked.connect(lambda: self._emit_if("en"))
        self._btn_ja.clicked.connect(lambda: self._emit_if("ja"))
        self.set_lang(lang)

    def set_lang(self, lang: str) -> None:
        self._updating = True
        ja = str(lang or "en").lower() == "ja"
        self._btn_ja.setChecked(ja)
        self._btn_en.setChecked(not ja)
        self._updating = False

    def lang(self) -> str:
        return "ja" if self._btn_ja.isChecked() else "en"

    def _emit_if(self, lang: str) -> None:
        if self._updating:
            return
        if self._on_change is not None:
            self._on_change(lang)
