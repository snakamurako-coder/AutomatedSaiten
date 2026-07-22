"""記述欄 OCR 言語・エンジンのトグル UI。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from ui_qt.style import COLORS

_FIELD_SEGMENT_STYLE = f"""
QFrame#OcrFieldSegmentTrack {{
    background: #e5e7eb;
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 8px;
}}
QFrame#OcrFieldSegmentTrack QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 8px;
    text-align: center;
    font-size: 11px;
    font-weight: 600;
    color: {COLORS["text_secondary"]};
    min-height: 24px;
    min-width: 48px;
}}
QFrame#OcrFieldSegmentTrack QPushButton:hover {{
    background: rgba(255, 255, 255, 0.45);
    color: {COLORS["text"]};
}}
QFrame#OcrFieldSegmentTrack QPushButton:checked {{
    background: {COLORS["accent"]};
    border-color: {COLORS["accent_hover"]};
    color: white;
    font-weight: 700;
}}
"""


def _refresh_field_segment_buttons(*buttons: QPushButton) -> None:
    for btn in buttons:
        style = btn.style()
        style.unpolish(btn)
        style.polish(btn)
        btn.update()


class _FieldSegmentBar(QFrame):
    """記述欄一覧向け — 背景トラック付きセグメントトグル。"""

    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OcrFieldSegmentTrack")
        self.setStyleSheet(_FIELD_SEGMENT_STYLE)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        for btn in buttons:
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            lay.addWidget(btn)


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
        lay.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btn_en = QPushButton("英語")
        self._btn_ja = QPushButton("日本語")
        self._group.addButton(self._btn_en)
        self._group.addButton(self._btn_ja)
        self._track = _FieldSegmentBar([self._btn_en, self._btn_ja], self)
        lay.addWidget(self._track)

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
        _refresh_field_segment_buttons(self._btn_en, self._btn_ja)

    def lang(self) -> str:
        return "ja" if self._btn_ja.isChecked() else "en"

    def _emit_if(self, lang: str) -> None:
        if self._updating:
            return
        _refresh_field_segment_buttons(self._btn_en, self._btn_ja)
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
        lay.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btn_openai = QPushButton("OpenAI")
        self._btn_vision = QPushButton("Vision")
        self._group.addButton(self._btn_openai)
        self._group.addButton(self._btn_vision)
        self._track = _FieldSegmentBar([self._btn_openai, self._btn_vision], self)
        lay.addWidget(self._track)

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
        _refresh_field_segment_buttons(self._btn_openai, self._btn_vision)

    def engine(self) -> str:
        return "vision" if self._btn_vision.isChecked() else "openai"

    def _emit_if(self, engine: str) -> None:
        if self._updating:
            return
        _refresh_field_segment_buttons(self._btn_openai, self._btn_vision)
        if self._on_change is not None:
            self._on_change(engine)
