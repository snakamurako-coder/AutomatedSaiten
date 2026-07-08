"""描画ツールフローティングパレット。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui_qt.crop_widgets import SliderSpinControls
from ui_qt.floating_palette.format_palette_panel import FormatPalettePanel
from ui_qt.floating_palette.phrase_palette_panel import PhrasePalettePanel
from ui_qt.floating_palette.palette_prefs import (
    PALETTE_COLORS,
    TOOL_ERASER,
    TOOL_NONE,
    TOOL_PEN,
    TOOL_PHRASE,
    TOOL_TEXT,
    VIEW_DETAILED,
    VIEW_SIMPLE,
)
from ui_qt.stylus_overlay import ERASER_MODE_PIXEL, ERASER_MODE_STROKE

MODE_DRAW = "draw"
MODE_TEXT = "text"
MODE_PHRASE = "phrase"


class ToolPaletteWindow(QWidget):
    """別ウィンドウ型描画ツールパレット（描画 / テキストの入力モードをタブで切替）。"""

    input_mode_changed = Signal(str)
    tool_changed = Signal(str)
    brush_changed = Signal(str, float, float)
    eraser_mode_changed = Signal(str)
    show_ink_changed = Signal(bool)
    show_text_changed = Signal(bool)
    view_mode_changed = Signal(str)
    minimize_requested = Signal()
    clear_ink_requested = Signal()
    clear_text_boxes_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("描画ツール")
        self.setObjectName("ToolPaletteWindow")
        self.resize(300, 480)
        self.setMinimumSize(260, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        self._title = QLabel("描画")
        self._title.setObjectName("FloatingPaletteTitle")
        header_row.addWidget(self._title)
        header_row.addStretch()
        self._undo_btn = QPushButton("↺")
        self._undo_btn.setObjectName("PaletteIconBtn")
        self._undo_btn.setToolTip("戻る — 直前の操作を取り消す（最大20件・Ctrl+Z）")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self.undo_requested.emit)
        header_row.addWidget(self._undo_btn)
        self._redo_btn = QPushButton("↻")
        self._redo_btn.setObjectName("PaletteIconBtn")
        self._redo_btn.setToolTip("やり直し — 戻した操作を復元（最大20件・Ctrl+Y）")
        self._redo_btn.setEnabled(False)
        self._redo_btn.clicked.connect(self.redo_requested.emit)
        header_row.addWidget(self._redo_btn)
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

        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self._mode_group = QButtonGroup(self)
        self._mode_draw_btn = self._make_tab_btn("描画")
        self._mode_text_btn = self._make_tab_btn("テキスト")
        self._mode_phrase_btn = self._make_tab_btn("定型文")
        for i, btn in enumerate(
            (self._mode_draw_btn, self._mode_text_btn, self._mode_phrase_btn)
        ):
            self._mode_group.addButton(btn, i)
            mode_row.addWidget(btn, 1)
        self._mode_draw_btn.setChecked(True)
        self._mode_draw_btn.toggled.connect(lambda c: c and self._switch_input_mode(MODE_DRAW))
        self._mode_text_btn.toggled.connect(lambda c: c and self._switch_input_mode(MODE_TEXT))
        self._mode_phrase_btn.toggled.connect(
            lambda c: c and self._switch_input_mode(MODE_PHRASE)
        )
        root.addLayout(mode_row)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root.addWidget(self._stack, 1)

        self._draw_page = QWidget()
        draw_lay = QVBoxLayout(self._draw_page)
        draw_lay.setContentsMargins(0, 0, 0, 0)
        draw_lay.setSpacing(8)

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
            label="太さ", min_val=1, max_val=20, value=3, label_width=36, spin_width=52
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
        draw_lay.addWidget(self._brush_frame)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self._draw_tool_group = QButtonGroup(self)
        self._draw_tool_group.setExclusive(False)
        self._pen_btn = self._make_tool_btn("ペン")
        self._eraser_btn = self._make_tool_btn("消しゴム")
        self._pen_btn.toggled.connect(self._on_pen_toggled)
        self._eraser_btn.toggled.connect(self._on_eraser_toggled)
        for i, btn in enumerate((self._pen_btn, self._eraser_btn)):
            self._draw_tool_group.addButton(btn, i)
            tools_row.addWidget(btn, 1)
        draw_lay.addLayout(tools_row)

        self._draw_hint = QLabel("スタイラスで手書き（パームリジェクション ON 時は常時描画）")
        self._draw_hint.setObjectName("PaletteHintLabel")
        self._draw_hint.setWordWrap(True)
        draw_lay.addWidget(self._draw_hint)

        clear_row = QVBoxLayout()
        clear_row.setSpacing(4)
        self._clear_ink_btn = QPushButton("選択画像のペン描写を全消去")
        self._clear_ink_btn.setToolTip("最後にクリックした画像の手書きをすべて消去")
        self._clear_ink_btn.clicked.connect(self.clear_ink_requested.emit)
        clear_row.addWidget(self._clear_ink_btn)
        self._clear_text_btn = QPushButton("選択画像のテキストボックスを全消去")
        self._clear_text_btn.setToolTip("最後にクリックした画像のテキストボックスをすべて消去")
        self._clear_text_btn.clicked.connect(self.clear_text_boxes_requested.emit)
        clear_row.addWidget(self._clear_text_btn)
        draw_lay.addLayout(clear_row)

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

        self._show_text_check = QCheckBox("テキストボックスレイヤー表示")
        self._show_text_check.setChecked(True)
        self._show_text_check.toggled.connect(self.show_text_changed.emit)
        detail_lay.addWidget(self._show_text_check)

        draw_lay.addWidget(self._detail_frame)
        draw_lay.addStretch()

        self._text_page = QWidget()
        text_lay = QVBoxLayout(self._text_page)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(8)

        self._text_hint = QLabel(
            "画像上でドラッグしてテキストボックスの大きさを決定\n"
            "配置後はダブルクリックで文字編集"
        )
        self._text_hint.setObjectName("PaletteHintLabel")
        self._text_hint.setWordWrap(True)
        text_lay.addWidget(self._text_hint)

        self._format_panel = FormatPalettePanel()
        self._text_format_scroll = QScrollArea()
        self._text_format_scroll.setWidgetResizable(True)
        self._text_format_scroll.setFrameShape(QFrame.NoFrame)
        self._text_format_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._text_format_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._text_format_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._text_format_scroll.setWidget(self._format_panel)
        text_lay.addWidget(self._text_format_scroll, 1)

        self._phrase_page = QWidget()
        phrase_lay = QVBoxLayout(self._phrase_page)
        phrase_lay.setContentsMargins(0, 0, 0, 0)
        phrase_lay.setSpacing(8)
        self._phrase_panel = PhrasePalettePanel()
        phrase_lay.addWidget(self._phrase_panel, 1)

        self._phrase_format_scroll = QScrollArea()
        self._phrase_format_scroll.setWidgetResizable(True)
        self._phrase_format_scroll.setFrameShape(QFrame.NoFrame)
        self._phrase_format_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._phrase_format_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._phrase_format_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._phrase_format_placeholder = QWidget()
        self._phrase_format_scroll.setWidget(self._phrase_format_placeholder)
        self._phrase_format_scroll.hide()
        phrase_lay.addWidget(self._phrase_format_scroll, 1)

        self._stack.addWidget(self._draw_page)
        self._stack.addWidget(self._text_page)
        self._stack.addWidget(self._phrase_page)

        self._view_mode = VIEW_SIMPLE
        self._input_mode = MODE_DRAW
        self._palm_rejection = True
        self._draw_tool = TOOL_NONE
        self._current_color = PALETTE_COLORS[0]
        self._pen_btn.setVisible(False)
        self._apply_view_mode()
        self._apply_palm_rejection_ui()
        self._switch_input_mode(MODE_DRAW, emit=False)
        self._emit_draw_tool(emit=False)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._clamp_geometry()

    def _clamp_geometry(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        max_h = max(self.minimumHeight(), avail.height() - 32)
        self.setMaximumHeight(max_h)
        geo = self.geometry()
        w = max(self.minimumWidth(), min(geo.width(), avail.width() - 16))
        h = max(self.minimumHeight(), min(geo.height(), max_h))
        x = min(max(geo.x(), avail.left()), max(avail.left(), avail.right() - w + 1))
        y = min(max(geo.y(), avail.top()), max(avail.top(), avail.bottom() - h + 1))
        if (geo.x(), geo.y(), geo.width(), geo.height()) != (x, y, w, h):
            self.setGeometry(x, y, w, h)

    @property
    def format_panel(self) -> FormatPalettePanel:
        return self._format_panel

    @property
    def phrase_panel(self) -> PhrasePalettePanel:
        return self._phrase_panel

    def _make_tab_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        return btn

    def _make_tool_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        return btn

    def _switch_input_mode(self, mode: str, *, emit: bool = True) -> None:
        mode = mode if mode in (MODE_DRAW, MODE_TEXT, MODE_PHRASE) else MODE_DRAW
        self._input_mode = mode
        if mode == MODE_TEXT:
            page = self._text_page
            title = "テキスト"
            active_btn = self._mode_text_btn
        elif mode == MODE_PHRASE:
            page = self._phrase_page
            title = "定型文"
            active_btn = self._mode_phrase_btn
        else:
            page = self._draw_page
            title = "描画"
            active_btn = self._mode_draw_btn
        self._stack.setCurrentWidget(page)
        self._title.setText(title)
        for btn in (self._mode_draw_btn, self._mode_text_btn, self._mode_phrase_btn):
            btn.blockSignals(True)
            btn.setChecked(btn is active_btn)
            btn.blockSignals(False)
        if emit:
            self.input_mode_changed.emit(mode)
            self._emit_active_tool()
        if mode != MODE_PHRASE:
            self.set_phrase_format_editor_visible(False)
        self._clamp_geometry()

    def show_draw_mode(self) -> None:
        self._switch_input_mode(MODE_DRAW)

    def show_text_mode(self) -> None:
        self._switch_input_mode(MODE_TEXT)

    def show_phrase_mode(self) -> None:
        self._switch_input_mode(MODE_PHRASE)

    def current_input_mode(self) -> str:
        return self._input_mode

    def _draw_tool_buttons(self) -> tuple[QPushButton, ...]:
        if self._palm_rejection:
            return (self._eraser_btn,)
        return (self._pen_btn, self._eraser_btn)

    def _uncheck_other_draw_tools(self, active: QPushButton) -> None:
        for btn in self._draw_tool_buttons():
            if btn is active:
                continue
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _on_pen_toggled(self, checked: bool) -> None:
        if self._input_mode != MODE_DRAW:
            return
        if checked:
            self._uncheck_other_draw_tools(self._pen_btn)
            self._draw_tool = TOOL_PEN
            self._emit_draw_tool()
        else:
            self._maybe_clear_draw_tool()

    def _on_eraser_toggled(self, checked: bool) -> None:
        if self._input_mode != MODE_DRAW:
            return
        if checked:
            self._uncheck_other_draw_tools(self._eraser_btn)
            self._draw_tool = TOOL_ERASER
            self._emit_draw_tool()
        else:
            self._maybe_clear_draw_tool()

    def _maybe_clear_draw_tool(self) -> None:
        if any(btn.isChecked() for btn in self._draw_tool_buttons()):
            return
        self._draw_tool = TOOL_NONE
        self._emit_draw_tool()

    def _sync_draw_tool_buttons(self) -> None:
        mapping = {TOOL_PEN: self._pen_btn, TOOL_ERASER: self._eraser_btn}
        for btn in (self._pen_btn, self._eraser_btn):
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        btn = mapping.get(self._draw_tool)
        if btn:
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)

    def _emit_draw_tool(self, *, emit: bool = True) -> None:
        tool = self._draw_tool
        if self._palm_rejection and tool == TOOL_PEN:
            tool = TOOL_NONE
        if emit:
            self.tool_changed.emit(tool)

    def _emit_active_tool(self) -> None:
        if self._input_mode == MODE_TEXT:
            self.tool_changed.emit(TOOL_TEXT)
        elif self._input_mode == MODE_PHRASE:
            self.tool_changed.emit(TOOL_PHRASE)
        else:
            self._emit_draw_tool()

    def set_undo_available(self, available: bool) -> None:
        self._undo_btn.setEnabled(bool(available))

    def set_redo_available(self, available: bool) -> None:
        self._redo_btn.setEnabled(bool(available))

    def set_palm_rejection(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._palm_rejection == enabled:
            return
        self._palm_rejection = enabled
        self._apply_palm_rejection_ui()
        if self._input_mode == MODE_DRAW:
            self._emit_active_tool()

    def _apply_palm_rejection_ui(self) -> None:
        self._pen_btn.setVisible(not self._palm_rejection)
        if self._palm_rejection:
            self._pen_btn.blockSignals(True)
            self._pen_btn.setChecked(False)
            self._pen_btn.blockSignals(False)
            if self._draw_tool == TOOL_PEN:
                self._draw_tool = TOOL_NONE
        self._draw_hint.setVisible(self._palm_rejection)

    def set_input_mode(self, mode: str) -> None:
        self._switch_input_mode(
            mode if mode in (MODE_DRAW, MODE_TEXT, MODE_PHRASE) else MODE_DRAW
        )

    def set_draw_tool(self, tool: str) -> None:
        tool = tool if tool in (TOOL_PEN, TOOL_ERASER, TOOL_NONE) else TOOL_NONE
        if self._palm_rejection and tool == TOOL_PEN:
            tool = TOOL_NONE
        self._draw_tool = tool
        self._sync_draw_tool_buttons()
        if self._input_mode == MODE_DRAW:
            self._emit_draw_tool()

    def set_tool(self, tool: str) -> None:
        """後方互換: TOOL_TEXT / TOOL_PHRASE なら各モード、それ以外は描画モード＋サブツール。"""
        if tool == TOOL_TEXT:
            self.set_input_mode(MODE_TEXT)
            return
        if tool == TOOL_PHRASE:
            self.set_input_mode(MODE_PHRASE)
            return
        self.set_input_mode(MODE_DRAW)
        self.set_draw_tool(tool)

    def clear_text_tool(self) -> None:
        self.show_draw_mode()

    def _toggle_view(self) -> None:
        self._view_mode = VIEW_DETAILED if self._view_mode == VIEW_SIMPLE else VIEW_SIMPLE
        self._apply_view_mode()
        self.view_mode_changed.emit(self._view_mode)

    def _apply_view_mode(self) -> None:
        detailed = self._view_mode == VIEW_DETAILED
        self._detail_frame.setVisible(detailed)
        self._format_panel.set_detailed_controls_visible(detailed)
        self._phrase_panel.set_view_mode(self._view_mode)
        self._view_btn.setText("簡易" if detailed else "詳細")
        self._clamp_geometry()

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()

    def _pick_color(self, color: str) -> None:
        self._current_color = color
        self._emit_brush()

    def set_text_palette_colors(self, colors: list[str] | tuple[str, ...]) -> None:
        self._format_panel.set_text_palette_colors(colors)

    def set_phrase_format_editor_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible:
            self._phrase_format_scroll.setWidget(self._format_panel)
            self._phrase_format_scroll.show()
            if self._text_format_scroll.widget() is self._format_panel:
                self._text_format_scroll.setWidget(QWidget())
        else:
            self._phrase_format_scroll.hide()
            self._phrase_format_scroll.setWidget(self._phrase_format_placeholder)
            if self._input_mode == MODE_TEXT:
                self._text_format_scroll.setWidget(self._format_panel)
        self._format_panel.set_template_edit_mode(visible)
        self._clamp_geometry()

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

    def set_show_text(self, visible: bool) -> None:
        self._show_text_check.setChecked(bool(visible))

    def current_brush(self) -> tuple[str, float, float]:
        return (
            self._current_color,
            float(self._width_ctrl.value()),
            float(self._alpha_ctrl.value()) / 100.0,
        )

    def current_tool(self) -> str:
        if self._input_mode == MODE_TEXT:
            return TOOL_TEXT
        if self._input_mode == MODE_PHRASE:
            return TOOL_PHRASE
        return self._draw_tool if not (self._palm_rejection and self._draw_tool == TOOL_PEN) else TOOL_NONE
