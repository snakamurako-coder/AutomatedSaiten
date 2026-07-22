"""記述欄 OCR 言語・エンジンのトグル UI。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from ui_qt.region_mode_widgets import _refresh_segment_button


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
            btn.setAutoDefault(False)
            btn.setDefault(False)
            _refresh_segment_button(btn)
            self._group.addButton(btn)
            lay.addWidget(btn)

        self._btn_en.clicked.connect(lambda: self._emit_if("en"))
        self._btn_ja.clicked.connect(lambda: self._emit_if("ja"))
        self.set_lang(lang)

    def set_lang(self, lang: str) -> None:
        self._updating = True
        self._group.blockSignals(True)
        ja = str(lang or "en").lower() == "ja"
        self._btn_ja.setChecked(ja)
        self._btn_en.setChecked(not ja)
        self._group.blockSignals(False)
        self._updating = False
        _refresh_segment_button(self._btn_en)
        _refresh_segment_button(self._btn_ja)

    def lang(self) -> str:
        return "ja" if self._btn_ja.isChecked() else "en"

    def _emit_if(self, lang: str) -> None:
        if self._updating:
            return
        _refresh_segment_button(self._btn_en)
        _refresh_segment_button(self._btn_ja)
        if self._on_change is not None:
            self._on_change(lang)


class OcrEngineToggle(QWidget):
    """OpenAI / Vision の排他トグル（内部値は openai / vision）。"""

    def __init__(
        self,
        engine: str = "openai",
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
        self._btn_openai = QPushButton("OpenAI")
        self._btn_vision = QPushButton("Vision")
        for btn in (self._btn_openai, self._btn_vision):
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            _refresh_segment_button(btn)
            self._group.addButton(btn)
            lay.addWidget(btn)

        self._btn_openai.clicked.connect(lambda: self._emit_if("openai"))
        self._btn_vision.clicked.connect(lambda: self._emit_if("vision"))
        self.set_engine(engine)

    def set_engine(self, engine: str) -> None:
        self._updating = True
        self._group.blockSignals(True)
        vision = str(engine or "openai").lower() == "vision"
        self._btn_vision.setChecked(vision)
        self._btn_openai.setChecked(not vision)
        self._group.blockSignals(False)
        self._updating = False
        _refresh_segment_button(self._btn_openai)
        _refresh_segment_button(self._btn_vision)

    def engine(self) -> str:
        return "vision" if self._btn_vision.isChecked() else "openai"

    def _emit_if(self, engine: str) -> None:
        if self._updating:
            return
        _refresh_segment_button(self._btn_openai)
        _refresh_segment_button(self._btn_vision)
        if self._on_change is not None:
            self._on_change(engine)
