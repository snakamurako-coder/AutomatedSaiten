"""描画ツールフローティングパレット。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt.crop_widgets import SliderSpinControls
from ui_qt.floating_palette.palette_prefs import (
    PALETTE_COLORS,
    TOOL_ERASER,
    TOOL_PEN,
    TOOL_TEXT,
    VIEW_DETAILED,
    VIEW_SIMPLE,
)
from ui_qt.stylus_overlay import ERASER_MODE_PIXEL, ERASER_MODE_STROKE


class ToolPaletteWindow(QWidget):
    """別ウィンドウ型描画ツールパレット。"""

    tool_changed = Signal(str)
    brush_changed = Signal(str, float, float)
    eraser_mode_changed = Signal(str)
    show_ink_changed = Signal(bool)
    view_mode_changed = Signal(str)
    minimize_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("描画ツール")
        self.setObjectName("ToolPaletteWindow")
        self.resize(280, 300)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        title = QLabel("描画ツール")
        title.setObjectName("FloatingPaletteTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        self._min_btn = QPushButton("−")
        self._min_btn.setObjectName("PaletteIconBtn")
        self._min_btn.setToolTip("最小化")
        self._min_btn.clicked.connect(self.minimize_requested.emit)
        header_row.addWidget(self._min_btn)
        self._view_btn = QPushButton("詳細")
        self._view_btn.setObjectName("PaletteIconBtn")
        self._view_btn.setFixedWidth(44)
        self._view_btn.setToolTip("表示切替")
        self._view_btn.clicked.connect(self._toggle_view)
        header_row.addWidget(self._view_btn)
        root.addLayout(header_row)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self._tool_group = QButtonGroup(self)
        self._pen_btn = self._make_tool_btn("ペン")
        self._eraser_btn = self._make_tool_btn("消しゴム")
        self._text_btn = self._make_tool_btn("テキスト")
        for i, btn in enumerate((self._pen_btn, self._eraser_btn, self._text_btn)):
            self._tool_group.addButton(btn, i)
            tools_row.addWidget(btn, 1)
        self._tool_group.idClicked.connect(self._on_tool_id)
        root.addLayout(tools_row)

        self._hint_label = QLabel("画像をクリックしてテキストボックスを配置")
        self._hint_label.setObjectName("PaletteHintLabel")
        self._hint_label.setWordWrap(True)
        self._hint_label.hide()
        root.addWidget(self._hint_label)

        self._brush_frame = QFrame()
        self._brush_frame.setObjectName("FloatingPaletteSection")
        brush_lay = QVBoxLayout(self._brush_frame)
        brush_lay.setContentsMargins(0, 0, 0, 0)
        brush_lay.setSpacing(8)

        colors_row = QHBoxLayout()
        colors_row.setSpacing(6)
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        for i, col in enumerate(PALETTE_COLORS):
            b = QPushButton()
            b.setObjectName("ColorSwatchBtn")
            b.setFixedSize(28, 28)
            b.setCheckable(True)
            b.setProperty("swatchColor", col)
            b.setStyleSheet(
                f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 14px; }}"
            )
            b.clicked.connect(lambda _c=False, c=col: self._pick_color(c))
            self._color_group.addButton(b, i)
            colors_row.addWidget(b)
            self._color_btns.append(b)
        colors_row.addStretch()
        brush_lay.addLayout(colors_row)

        self._width_ctrl = SliderSpinControls(
            label="太さ",
            min_val=1,
            max_val=20,
            value=3,
            label_width=36,
            spin_width=52,
        )
        self._width_ctrl.valueChanged.connect(lambda _v: self._emit_brush())
        brush_lay.addWidget(self._width_ctrl)

        self._alpha_ctrl = SliderSpinControls(
            label="透明度",
            min_val=10,
            max_val=100,
            value=100,
            suffix=" %",
            label_width=48,
            spin_width=64,
        )
        self._alpha_ctrl.valueChanged.connect(lambda _v: self._emit_brush())
        brush_lay.addWidget(self._alpha_ctrl)
        root.addWidget(self._brush_frame)

        self._detail_frame = QFrame()
        self._detail_frame.setObjectName("FloatingPaletteSection")
        detail_lay = QVBoxLayout(self._detail_frame)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.setSpacing(8)

        eraser_row = QHBoxLayout()
        eraser_lbl = QLabel("消しゴム")
        eraser_lbl.setFixedWidth(48)
        eraser_row.addWidget(eraser_lbl)
        self._eraser_combo = QComboBox()
        self._eraser_combo.addItem("ピクセル", ERASER_MODE_PIXEL)
        self._eraser_combo.addItem("ストローク", ERASER_MODE_STROKE)
        self._eraser_combo.currentIndexChanged.connect(
            lambda _i: self.eraser_mode_changed.emit(self._eraser_combo.currentData())
        )
        eraser_row.addWidget(self._eraser_combo, 1)
        detail_lay.addLayout(eraser_row)

        self._show_ink_check = QCheckBox("手書きレイヤー表示")
        self._show_ink_check.setChecked(True)
        self._show_ink_check.toggled.connect(self.show_ink_changed.emit)
        detail_lay.addWidget(self._show_ink_check)
        root.addWidget(self._detail_frame)

        root.addStretch()

        self._view_mode = VIEW_SIMPLE
        self._current_tool = TOOL_PEN
        self._current_color = PALETTE_COLORS[0]
        self._apply_view_mode()
        self._set_tool(TOOL_PEN)

    def _make_tool_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        return btn

    def _toggle_view(self) -> None:
        self._view_mode = VIEW_DETAILED if self._view_mode == VIEW_SIMPLE else VIEW_SIMPLE
        self._apply_view_mode()
        self.view_mode_changed.emit(self._view_mode)

    def _apply_view_mode(self) -> None:
        detailed = self._view_mode == VIEW_DETAILED
        self._detail_frame.setVisible(detailed)
        self._view_btn.setText("簡易" if detailed else "詳細")

    def _update_tool_ui(self, tool: str) -> None:
        is_text = tool == TOOL_TEXT
        self._hint_label.setVisible(is_text)
        self._brush_frame.setVisible(not is_text)

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()

    def _on_tool_id(self, tool_id: int) -> None:
        tools = (TOOL_PEN, TOOL_ERASER, TOOL_TEXT)
        if 0 <= tool_id < len(tools):
            self._set_tool(tools[tool_id])

    def _set_tool(self, tool: str) -> None:
        self._current_tool = tool
        mapping = {TOOL_PEN: self._pen_btn, TOOL_ERASER: self._eraser_btn, TOOL_TEXT: self._text_btn}
        btn = mapping.get(tool)
        if btn:
            btn.setChecked(True)
        self._update_tool_ui(tool)
        self.tool_changed.emit(tool)

    def set_tool(self, tool: str) -> None:
        self._set_tool(tool)

    def _pick_color(self, color: str) -> None:
        self._current_color = color
        self._emit_brush()

    def _emit_brush(self) -> None:
        w = float(self._width_ctrl.value())
        a = float(self._alpha_ctrl.value()) / 100.0
        self.brush_changed.emit(self._current_color, w, a)

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self._current_color = color
        self._width_ctrl.block_slider_signals(True)
        self._width_ctrl.block_spin_signals(True)
        self._alpha_ctrl.block_slider_signals(True)
        self._alpha_ctrl.block_spin_signals(True)
        self._width_ctrl.set_value(max(1, min(20, int(round(width)))))
        self._alpha_ctrl.set_value(max(10, min(100, int(round(alpha * 100)))))
        self._width_ctrl.block_slider_signals(False)
        self._width_ctrl.block_spin_signals(False)
        self._alpha_ctrl.block_slider_signals(False)
        self._alpha_ctrl.block_spin_signals(False)
        for i, col in enumerate(PALETTE_COLORS):
            if col.lower() == color.lower():
                self._color_btns[i].setChecked(True)
                break

    def set_eraser_mode(self, mode: str) -> None:
        idx = self._eraser_combo.findData(mode)
        if idx >= 0:
            self._eraser_combo.setCurrentIndex(idx)

    def set_show_ink(self, visible: bool) -> None:
        self._show_ink_check.setChecked(bool(visible))

    def current_brush(self) -> tuple[str, float, float]:
        return (
            self._current_color,
            float(self._width_ctrl.value()),
            float(self._alpha_ctrl.value()) / 100.0,
        )
