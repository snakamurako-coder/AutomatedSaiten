"""テキスト書式パネル（描画ツールウィンドウ内タブ用）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt.crop_widgets import SliderSpinControls


class FormatPalettePanel(QWidget):
    """テキストボックス選択時の書式コントロール。"""

    style_changed = Signal(dict)
    edit_done_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._border_w = SliderSpinControls(
            label="枠太さ",
            min_val=1,
            max_val=10,
            value=2,
            label_width=72,
            spin_width=52,
        )
        self._border_w.valueChanged.connect(lambda _v: self._emit_style())
        root.addWidget(self._border_w)

        self._border_a = SliderSpinControls(
            label="枠透明度",
            min_val=0,
            max_val=100,
            value=100,
            suffix=" %",
            label_width=72,
            spin_width=64,
        )
        self._border_a.valueChanged.connect(lambda _v: self._emit_style())
        root.addWidget(self._border_a)

        self._fill_a = SliderSpinControls(
            label="背景透明度",
            min_val=0,
            max_val=100,
            value=85,
            suffix=" %",
            label_width=72,
            spin_width=64,
        )
        self._fill_a.valueChanged.connect(lambda _v: self._emit_style())
        root.addWidget(self._fill_a)

        self._font_size = SliderSpinControls(
            label="文字サイズ",
            min_val=8,
            max_val=48,
            value=14,
            label_width=72,
            spin_width=52,
        )
        self._font_size.valueChanged.connect(lambda _v: self._emit_style())
        root.addWidget(self._font_size)

        self._bold_check = QCheckBox("太字")
        self._bold_check.toggled.connect(self._emit_style)
        root.addWidget(self._bold_check)
        self._underline_check = QCheckBox("下線")
        self._underline_check.toggled.connect(self._emit_style)
        root.addWidget(self._underline_check)

        align_row = QHBoxLayout()
        align_lbl = QLabel("揃え")
        align_lbl.setFixedWidth(72)
        align_row.addWidget(align_lbl)
        self._align_combo = QComboBox()
        self._align_combo.addItems(["left", "center", "right"])
        self._align_combo.currentTextChanged.connect(self._emit_style)
        align_row.addWidget(self._align_combo, 1)
        root.addLayout(align_row)

        self._vertical_check = QCheckBox("縦書き")
        self._vertical_check.toggled.connect(self._emit_style)
        root.addWidget(self._vertical_check)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        done_btn = QPushButton("編集完了")
        done_btn.clicked.connect(self.edit_done_requested.emit)
        edit_btn = QPushButton("文字を編集")
        edit_btn.clicked.connect(self.edit_requested.emit)
        del_btn = QPushButton("削除")
        del_btn.setProperty("variant", "danger")
        del_btn.clicked.connect(self.delete_requested.emit)
        btn_row.addWidget(done_btn, 1)
        btn_row.addWidget(edit_btn, 1)
        btn_row.addWidget(del_btn, 1)
        root.addLayout(btn_row)
        root.addStretch()

    def load_style(self, style: dict[str, Any]) -> None:
        st = style or {}
        for ctrl in (self._border_w, self._border_a, self._fill_a, self._font_size):
            ctrl.block_slider_signals(True)
            ctrl.block_spin_signals(True)
        self._border_w.set_value(max(1, min(10, int(st.get("borderWidth") or 2))))
        self._border_a.set_value(int(float(st.get("borderAlpha") or 1) * 100))
        self._fill_a.set_value(int(float(st.get("fillAlpha") or 0.85) * 100))
        self._font_size.set_value(int(st.get("fontSize") or 14))
        for ctrl in (self._border_w, self._border_a, self._fill_a, self._font_size):
            ctrl.block_slider_signals(False)
            ctrl.block_spin_signals(False)
        self._bold_check.setChecked(bool(st.get("bold")))
        self._underline_check.setChecked(bool(st.get("underline")))
        self._vertical_check.setChecked(bool(st.get("vertical")))
        align = str(st.get("align") or "left")
        idx = self._align_combo.findText(align)
        if idx >= 0:
            self._align_combo.setCurrentIndex(idx)

    def _emit_style(self) -> None:
        self.style_changed.emit(
            {
                "borderWidth": self._border_w.value(),
                "borderAlpha": self._border_a.value() / 100.0,
                "fillAlpha": self._fill_a.value() / 100.0,
                "fontSize": self._font_size.value(),
                "bold": self._bold_check.isChecked(),
                "underline": self._underline_check.isChecked(),
                "vertical": self._vertical_check.isChecked(),
                "align": self._align_combo.currentText(),
            }
        )
