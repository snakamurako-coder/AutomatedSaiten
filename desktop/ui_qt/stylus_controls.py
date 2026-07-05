"""スタイラス手書きの共通コントロール。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QWidget

from ui_qt import helpers as h
from ui_qt.stylus_prefs import (
    ERASER_MODE_PIXEL,
    ERASER_MODE_STROKE,
    load_stylus_prefs,
    save_stylus_eraser_mode,
)


class StylusControls(QWidget):
    """消しゴム種別・手書きレイヤー表示の切替。"""

    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        prefs = load_stylus_prefs()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(h.caption_label("消しゴム:"))
        self.eraser_combo = QComboBox()
        self.eraser_combo.addItem("ピクセル消しゴム", ERASER_MODE_PIXEL)
        self.eraser_combo.addItem("ストローク消しゴム", ERASER_MODE_STROKE)
        idx = self.eraser_combo.findData(prefs["eraser_mode"])
        self.eraser_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.eraser_combo.setToolTip(
            "ピクセル: なぞった部分だけ消去\n"
            "ストローク: 触れた一筆書き全体を消去"
        )
        self.eraser_combo.currentIndexChanged.connect(self._on_eraser_mode_changed)
        lay.addWidget(self.eraser_combo)

        self.show_ink_check = QCheckBox("手書きレイヤー表示")
        self.show_ink_check.setChecked(True)
        self.show_ink_check.setToolTip("手書き内容の表示／非表示（データは保持）")
        self.show_ink_check.toggled.connect(lambda _v: self.settings_changed.emit())
        lay.addWidget(self.show_ink_check)

        lay.addWidget(h.caption_label("スタイラス＝最前面レイヤー"))
        lay.addStretch()

    def _on_eraser_mode_changed(self, _index: int) -> None:
        save_stylus_eraser_mode(self.eraser_mode())
        self.settings_changed.emit()

    def eraser_mode(self) -> str:
        mode = self.eraser_combo.currentData()
        return str(mode or ERASER_MODE_PIXEL)

    def show_ink_layer(self) -> bool:
        return self.show_ink_check.isChecked()

    def set_eraser_mode(self, mode: str) -> None:
        idx = self.eraser_combo.findData(mode)
        if idx >= 0:
            self.eraser_combo.setCurrentIndex(idx)

    def set_show_ink_layer(self, visible: bool) -> None:
        self.show_ink_check.setChecked(bool(visible))
