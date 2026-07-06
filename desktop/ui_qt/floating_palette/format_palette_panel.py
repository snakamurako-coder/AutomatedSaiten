"""テキスト書式パネル（テンプレート・文字色・基本操作のみ）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
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
from ui_qt.style import COLORS


class FormatPalettePanel(QWidget):
    """テキストボックス選択時の最小書式コントロール。"""

    style_changed = Signal(dict)
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
        self._speech_phase = "idle"
        self._style: dict[str, Any] = dict(TEXT_STYLE_TEMPLATES["A"])
        self._text_palette_colors: tuple[str, ...] = TEXT_PALETTE_COLORS
        self._rebuild_color_swatches()
        self._sync_color_swatches(TEXT_PALETTE_COLORS[0])

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
        self._sync_color_swatches(color)
        self._style["textColor"] = color
        self._emit_style()

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
        self._sync_color_swatches(
            str(self._style.get("textColor") or self._text_palette_colors[0])
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
                "Windows 音声入力（Win+H）を起動。"
                "画面上部の音声入力バーで話すと、テキストボックスに直接入力されます。"
            )
        else:
            self._speech_btn.setToolTip(
                "マイクで音声をテキストに追加"
                "（無言で区切ると認識。認識中にもう一度押すと終了）"
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
