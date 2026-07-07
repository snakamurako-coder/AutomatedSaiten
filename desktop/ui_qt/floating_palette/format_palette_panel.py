"""テキスト書式パネル（テンプレート・文字色・サイズ・装飾）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    TEXT_PALETTE_COLORS,
    TEXT_STYLE_TEMPLATES,
    resolve_text_style,
)
from ui_qt.style import COLORS


class FormatPalettePanel(QWidget):
    """テキストボックス選択時の書式コントロール。"""

    style_changed = Signal(dict)
    char_format_changed = Signal(dict)
    edit_done_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()
    speech_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(6)
        tpl_lbl = QLabel("背景")
        tpl_lbl.setFixedWidth(48)
        tpl_row.addWidget(tpl_lbl)
        tpl_a = QPushButton("なし")
        tpl_a.setToolTip("文字のみ（背景・枠なし）")
        tpl_a.clicked.connect(lambda: self.apply_template("A"))
        tpl_b = QPushButton("半透明")
        tpl_b.setToolTip("文字色の補色を20%で背景表示")
        tpl_b.clicked.connect(lambda: self.apply_template("B"))
        tpl_row.addWidget(tpl_a, 1)
        tpl_row.addWidget(tpl_b, 1)
        root.addLayout(tpl_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        color_lbl = QLabel("文字色")
        color_lbl.setFixedWidth(48)
        color_row.addWidget(color_lbl)
        self._color_row = color_row
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        root.addLayout(color_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        size_lbl = QLabel("サイズ")
        size_lbl.setFixedWidth(48)
        size_row.addWidget(size_lbl)
        self._size_spin = self._make_pt_spin(
            "選択範囲またはカーソル位置の文字サイズ"
        )
        self._size_spin.valueChanged.connect(self._on_size_changed)
        size_row.addWidget(self._size_spin)
        size_row.addStretch()
        root.addLayout(size_row)

        spacing_row = QHBoxLayout()
        spacing_row.setSpacing(6)
        spacing_lbl = QLabel("行間")
        spacing_lbl.setFixedWidth(48)
        spacing_row.addWidget(spacing_lbl)
        self._line_spacing_spin = self._make_pt_spin("改行後の行の高さ（ポイント）")
        self._line_spacing_spin.valueChanged.connect(self._on_line_spacing_changed)
        spacing_row.addWidget(self._line_spacing_spin)
        spacing_row.addStretch()
        root.addLayout(spacing_row)

        self._detail_format_frame = QFrame()
        detail_lay = QHBoxLayout(self._detail_format_frame)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.setSpacing(6)
        deco_lbl = QLabel("装飾")
        deco_lbl.setFixedWidth(48)
        detail_lay.addWidget(deco_lbl)
        self._bold_btn = self._make_deco_btn("太字", "太字", bold=True)
        self._italic_btn = self._make_deco_btn("イタリック", "イタリック", italic=True)
        self._underline_btn = self._make_deco_btn("下線", "下線", underline=True)
        self._bold_btn.clicked.connect(lambda: self._emit_toggle("toggleBold"))
        self._italic_btn.clicked.connect(lambda: self._emit_toggle("toggleItalic"))
        self._underline_btn.clicked.connect(lambda: self._emit_toggle("toggleUnderline"))
        detail_lay.addWidget(self._bold_btn, 1)
        detail_lay.addWidget(self._italic_btn, 1)
        detail_lay.addWidget(self._underline_btn, 1)
        root.addWidget(self._detail_format_frame)
        self._detail_format_frame.hide()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        done_btn = QPushButton("編集完了")
        done_btn.clicked.connect(self.edit_done_requested.emit)
        edit_btn = QPushButton("文字を編集")
        edit_btn.setToolTip("ダブルクリックでも編集を開始できます")
        edit_btn.clicked.connect(self.edit_requested.emit)
        self._speech_btn = QPushButton("音声入力")
        self._speech_btn.setCheckable(True)
        self._speech_btn.setToolTip(
            "マイクで音声をテキストに追加"
            "（要ネット・話したあと少し間を空けると認識されます）"
        )
        self._speech_btn.toggled.connect(self._on_speech_toggled)
        del_btn = QPushButton("削除")
        del_btn.setToolTip("選択中のテキストボックスを削除（Del キーでも可）")
        del_btn.setProperty("variant", "danger")
        del_btn.clicked.connect(self.delete_requested.emit)
        btn_row.addWidget(done_btn, 1)
        btn_row.addWidget(edit_btn, 1)
        btn_row.addWidget(self._speech_btn, 1)
        btn_row.addWidget(del_btn, 1)
        root.addLayout(btn_row)

        self._speech_status_label = QLabel("")
        self._speech_status_label.setObjectName("PaletteHintLabel")
        self._speech_status_label.setWordWrap(True)
        self._speech_status_label.hide()
        root.addWidget(self._speech_status_label)

        root.addStretch()

        self._loading = False
        self._loading_char = False
        self._speech_phase = "idle"
        self._style: dict[str, Any] = dict(TEXT_STYLE_TEMPLATES["A"])
        self._text_palette_colors: tuple[str, ...] = TEXT_PALETTE_COLORS
        self._rebuild_color_swatches()
        self._sync_char_format_ui(
            {
                "color": TEXT_PALETTE_COLORS[0],
                "fontSize": 14,
                "lineSpacing": 14,
                "bold": False,
                "italic": False,
                "underline": False,
            }
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, 240)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(260, 320)

    def _make_pt_spin(self, tooltip: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(6, 72)
        spin.setSuffix(" pt")
        spin.setToolTip(tooltip)
        fm = QFontMetrics(spin.font())
        frame = spin.style().pixelMetric(QStyle.PixelMetric.PM_SpinBoxFrameWidth, None, spin)
        spin.setFixedWidth(fm.horizontalAdvance("14 pt") + frame * 2 + 20)
        return spin

    def _make_deco_btn(
        self,
        label: str,
        tooltip: str,
        *,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        font = QFont(btn.font())
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        btn.setFont(font)
        return btn

    def set_detailed_controls_visible(self, visible: bool) -> None:
        self._detail_format_frame.setVisible(bool(visible))

    def set_text_palette_colors(self, colors: list[str] | tuple[str, ...]) -> None:
        if len(colors) != 6:
            return
        self._text_palette_colors = tuple(str(c) for c in colors)
        self._rebuild_color_swatches()
        tc = str(self._style.get("textColor") or self._text_palette_colors[0])
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
        merged = {**self._style, **tpl}
        merged["textColor"] = str(
            self._style.get("textColor") or self._text_palette_colors[0]
        )
        self.load_style(merged)
        self._emit_style()

    def _pick_text_color(self, color: str) -> None:
        if self._loading_char:
            return
        self._sync_color_swatches(color)
        self._style["textColor"] = color
        self.char_format_changed.emit({"color": str(color)})

    def _on_size_changed(self, value: int) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({"fontSize": int(value)})

    def _on_line_spacing_changed(self, value: int) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({"lineSpacing": int(value)})

    def _emit_toggle(self, key: str) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({key: True})

    def sync_char_format(self, state: dict[str, Any]) -> None:
        self._sync_char_format_ui(state)

    def _sync_char_format_ui(self, state: dict[str, Any]) -> None:
        self._loading_char = True
        try:
            color = str(state.get("color") or self._text_palette_colors[0])
            self._sync_color_swatches(color)
            self._style["textColor"] = color
            size = int(state.get("fontSize") or self._style.get("fontSize") or 14)
            self._size_spin.blockSignals(True)
            self._size_spin.setValue(max(6, min(72, size)))
            self._size_spin.blockSignals(False)
            spacing = int(
                state.get("lineSpacing")
                or self._style.get("lineSpacing")
                or size
            )
            self._line_spacing_spin.blockSignals(True)
            self._line_spacing_spin.setValue(max(6, min(72, spacing)))
            self._line_spacing_spin.blockSignals(False)
            self._bold_btn.blockSignals(True)
            self._italic_btn.blockSignals(True)
            self._underline_btn.blockSignals(True)
            self._bold_btn.setChecked(bool(state.get("bold")))
            self._italic_btn.setChecked(bool(state.get("italic")))
            self._underline_btn.setChecked(bool(state.get("underline")))
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(False)
        finally:
            self._loading_char = False

    def _sync_color_swatches(self, color: str) -> None:
        for i, col in enumerate(self._text_palette_colors):
            if col.lower() == color.lower():
                self._color_btns[i].setChecked(True)
                return
        for btn in self._color_btns:
            btn.setChecked(False)

    def load_style(self, style: dict[str, Any]) -> None:
        self._loading = True
        self._style = resolve_text_style(style)
        self._sync_char_format_ui(
            {
                "color": str(self._style.get("textColor") or self._text_palette_colors[0]),
                "fontSize": int(self._style.get("fontSize") or 14),
                "lineSpacing": int(
                    self._style.get("lineSpacing")
                    or self._style.get("fontSize")
                    or 14
                ),
                "bold": False,
                "italic": False,
                "underline": False,
            }
        )
        self._loading = False

    def _emit_style(self) -> None:
        if self._loading:
            return
        resolved = resolve_text_style(self._style)
        self._style = resolved
        self.style_changed.emit(resolved)

    def _on_speech_toggled(self, on: bool) -> None:
        self.speech_toggled.emit(bool(on))

    def set_speech_mode(self, mode: str) -> None:
        if mode == "windows":
            self._speech_btn.setToolTip(
                "テキストボックス選択中は Windows 音声入力（Win+H）。"
                "未選択時はアプリ内認識で話し、確定後に配置場所をクリック。"
            )
        else:
            self._speech_btn.setToolTip(
                "マイクで音声をテキストに追加"
                "（無言で区切ると認識。認識中にもう一度押すと終了。"
                " テキストボックス未選択時は配置場所をクリック）"
            )

    def release_speech_button_focus(self) -> None:
        self._speech_btn.clearFocus()
        self.clearFocus()

    def is_speech_checked(self) -> bool:
        return self._speech_btn.isChecked()

    def set_speech_available(self, available: bool) -> None:
        self._speech_btn.setEnabled(bool(available))
        if not available:
            self.set_speech_active(False)

    def set_speech_active(self, on: bool) -> None:
        if self._speech_btn.isChecked() == on:
            if not on:
                self.set_speech_phase("idle")
            return
        self._speech_btn.blockSignals(True)
        self._speech_btn.setChecked(on)
        self._speech_btn.blockSignals(False)
        if not on:
            self.set_speech_phase("idle")

    def set_speech_phase(self, phase: str) -> None:
        """音声入力の状態表示（idle / preparing / recognizing / paused / windows）。"""
        self._speech_phase = str(phase or "idle")
        btn_text = "音声入力"
        status = ""
        accent = False
        if self._speech_phase == "preparing":
            btn_text = "準備中…"
            status = "マイクを準備しています…"
            accent = True
        elif self._speech_phase == "recognizing":
            btn_text = "認識中…"
            status = "話してください。もう一度押すとその時点まで認識して終了します。"
            accent = True
        elif self._speech_phase == "paused":
            btn_text = "確認中…"
            status = "認識結果の確認中です。"
            accent = True
        elif self._speech_phase == "windows":
            btn_text = "音声入力中…"
            status = "Windows 音声入力を使用中です。"
            accent = True
        elif self._speech_phase == "placing":
            btn_text = "配置待ち…"
            status = "認識したテキストを配置する場所をクリックしてください。"
            accent = True
        self._speech_btn.setText(btn_text)
        if accent:
            self._speech_btn.setStyleSheet(
                f"QPushButton {{ background: {COLORS['accent_soft']}; font-weight: 600; }}"
            )
        else:
            self._speech_btn.setStyleSheet("")
        if status:
            self._speech_status_label.setText(status)
            self._speech_status_label.show()
        else:
            self._speech_status_label.clear()
            self._speech_status_label.hide()
