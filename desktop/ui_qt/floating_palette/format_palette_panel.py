"""テキスト書式パネル（描画ツールウィンドウ内タブ用）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    TEXT_PALETTE_COLORS,
    TEXT_STYLE_TEMPLATES,
    resolve_text_style,
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

        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(6)
        tpl_lbl = QLabel("テンプレート")
        tpl_lbl.setFixedWidth(72)
        tpl_row.addWidget(tpl_lbl)
        tpl_a = QPushButton("A: 文字のみ")
        tpl_a.setToolTip("背景・枠なし（文字だけ表示）")
        tpl_a.clicked.connect(lambda: self.apply_template("A"))
        tpl_b = QPushButton("B: 半透明")
        tpl_b.setToolTip("文字色の補色を半透明背景に（文字色は下の6色から選択）")
        tpl_b.clicked.connect(lambda: self.apply_template("B"))
        tpl_row.addWidget(tpl_a, 1)
        tpl_row.addWidget(tpl_b, 1)
        root.addLayout(tpl_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        color_lbl = QLabel("文字色")
        color_lbl.setFixedWidth(72)
        color_row.addWidget(color_lbl)
        self._color_row = color_row
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        root.addLayout(color_row)

        self._border_w = SliderSpinControls(
            label="枠太さ",
            min_val=0,
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

        self._loading = False
        self._style_extras: dict[str, Any] = {}
        self._text_palette_colors: tuple[str, ...] = TEXT_PALETTE_COLORS
        self._rebuild_color_swatches()
        self._sync_color_swatches(TEXT_PALETTE_COLORS[0])

    def set_text_palette_colors(self, colors: list[str] | tuple[str, ...]) -> None:
        if len(colors) != 6:
            return
        self._text_palette_colors = tuple(str(c) for c in colors)
        self._rebuild_color_swatches()
        tc = str(self._style_extras.get("textColor") or self._text_palette_colors[0])
        self._sync_color_swatches(tc)

    def _rebuild_color_swatches(self) -> None:
        for btn in self._color_btns:
            self._color_group.removeButton(btn)
            btn.deleteLater()
        self._color_btns.clear()
        while self._color_row.count() > 1:
            item = self._color_row.takeAt(self._color_row.count() - 1)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for i, col in enumerate(self._text_palette_colors):
            b = QPushButton()
            b.setObjectName("ColorSwatchBtn")
            b.setFixedSize(28, 28)
            b.setCheckable(True)
            b.setStyleSheet(
                f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 14px; }}"
            )
            b.clicked.connect(lambda _c=False, idx=i: self._pick_text_color_by_index(idx))
            self._color_group.addButton(b, i)
            self._color_row.addWidget(b)
            self._color_btns.append(b)
        self._color_row.addStretch()

    def _pick_text_color_by_index(self, index: int) -> None:
        if 0 <= index < len(self._text_palette_colors):
            self._pick_text_color(self._text_palette_colors[index])

    def apply_template(self, key: str) -> None:
        tpl = TEXT_STYLE_TEMPLATES.get(key)
        if not tpl:
            return
        base = dict(tpl)
        tc = str(self._style_extras.get("textColor") or self._text_palette_colors[0])
        base["textColor"] = tc
        resolved = resolve_text_style({**self.current_style(), **base})
        self.load_style(resolved)
        self.style_changed.emit(resolved)

    def _pick_text_color(self, color: str) -> None:
        self._sync_color_swatches(color)
        partial = {**self.current_style(), **self._style_extras, "textColor": color}
        resolved = resolve_text_style(partial)
        self._style_extras.update(
            {
                k: resolved[k]
                for k in ("textColor", "fillColor", "borderColor", "templateId")
                if k in resolved
            }
        )
        self._emit_style()

    def _sync_color_swatches(self, color: str) -> None:
        for i, col in enumerate(self._text_palette_colors):
            if col.lower() == color.lower():
                self._color_btns[i].setChecked(True)
                return
        for btn in self._color_btns:
            btn.setChecked(False)

    def current_style(self) -> dict[str, Any]:
        return {
            "borderWidth": self._border_w.value(),
            "borderAlpha": self._border_a.value() / 100.0,
            "fillAlpha": self._fill_a.value() / 100.0,
            "fontSize": self._font_size.value(),
            "bold": self._bold_check.isChecked(),
            "underline": self._underline_check.isChecked(),
            "vertical": self._vertical_check.isChecked(),
            "align": self._align_combo.currentText(),
        }

    def load_style(self, style: dict[str, Any]) -> None:
        resolved = resolve_text_style(style)
        for k in ("borderColor", "fillColor", "textColor", "fontFamily", "templateId"):
            if k in resolved:
                self._style_extras[k] = resolved[k]
        self._sync_color_swatches(str(resolved.get("textColor") or self._text_palette_colors[0]))
        self._loading = True
        for ctrl in (self._border_w, self._border_a, self._fill_a, self._font_size):
            ctrl.block_slider_signals(True)
            ctrl.block_spin_signals(True)
        self._border_w.set_value(max(0, min(10, int(resolved.get("borderWidth", 2)))))
        self._border_a.set_value(int(float(resolved.get("borderAlpha", 1.0)) * 100))
        self._fill_a.set_value(int(float(resolved.get("fillAlpha", 0.85)) * 100))
        self._font_size.set_value(int(resolved.get("fontSize") or 14))
        for ctrl in (self._border_w, self._border_a, self._fill_a, self._font_size):
            ctrl.block_slider_signals(False)
            ctrl.block_spin_signals(False)
        self._bold_check.blockSignals(True)
        self._underline_check.blockSignals(True)
        self._vertical_check.blockSignals(True)
        self._align_combo.blockSignals(True)
        self._bold_check.setChecked(bool(resolved.get("bold")))
        self._underline_check.setChecked(bool(resolved.get("underline")))
        self._vertical_check.setChecked(bool(resolved.get("vertical")))
        align = str(resolved.get("align") or "left")
        idx = self._align_combo.findText(align)
        if idx >= 0:
            self._align_combo.setCurrentIndex(idx)
        self._bold_check.blockSignals(False)
        self._underline_check.blockSignals(False)
        self._vertical_check.blockSignals(False)
        self._align_combo.blockSignals(False)
        self._loading = False

    def _emit_style(self) -> None:
        if self._loading:
            return
        partial = {**self._style_extras, **self.current_style()}
        resolved = resolve_text_style(partial)
        self._style_extras.update(
            {
                k: resolved[k]
                for k in ("textColor", "fillColor", "borderColor", "templateId")
                if k in resolved
            }
        )
        self.style_changed.emit(resolved)
