"""テキスト書式フローティングパレット。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
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
from ui_qt.floating_palette.format_palette_placer import place_format_palette


class FormatPaletteWindow(QWidget):
    """テキストボックス選択時の書式パレット。"""

    style_changed = Signal(dict)
    edit_requested = Signal()
    delete_requested = Signal()
    pin_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("テキスト書式")
        self.setObjectName("FormatPaletteWindow")
        self.resize(280, 340)
        self._pinned = False
        self._pinned_pos: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        title = QLabel("テキスト書式")
        title.setObjectName("FloatingPaletteTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        self._pin_btn = QPushButton("📌")
        self._pin_btn.setObjectName("PaletteIconBtn")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setToolTip("位置を固定")
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        header_row.addWidget(self._pin_btn)
        root.addLayout(header_row)

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
        edit_btn = QPushButton("文字を編集")
        edit_btn.clicked.connect(self.edit_requested.emit)
        del_btn = QPushButton("削除")
        del_btn.setProperty("variant", "danger")
        del_btn.clicked.connect(self.delete_requested.emit)
        btn_row.addWidget(edit_btn, 1)
        btn_row.addWidget(del_btn, 1)
        root.addLayout(btn_row)

    def _on_pin_toggled(self, pinned: bool) -> None:
        self._pinned = bool(pinned)
        if self._pinned:
            self._pinned_pos = self.pos()
        else:
            self._pinned_pos = None
        self.pin_changed.emit(self._pinned)

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

    def reposition_near(
        self,
        box_global_rect,
        *,
        viewer_global=None,
    ) -> None:
        if self._pinned and self._pinned_pos is not None:
            return
        pos = place_format_palette(
            box_global_rect,
            (self.width(), self.height()),
            viewer_global=viewer_global,
            pinned_pos=None,
        )
        self.move(pos)

    def clear_pin(self) -> None:
        self._pinned = False
        self._pinned_pos = None
        self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(False)
        self._pin_btn.blockSignals(False)
        self.pin_changed.emit(False)

    def hide_palette(self) -> None:
        self.clear_pin()
        self.hide()
