"""描画ツールフローティングパレット。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QShowEvent
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
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ui_qt.crop_widgets import SliderSpinControls
from ui_qt.floating_palette.format_palette_panel import FormatPalettePanel
from ui_qt.floating_palette.phrase_edit_preview_panel import PhraseEditPreviewPanel
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

_PALETTE_MIN_WIDTH = 236
_PALETTE_MIN_HEIGHT = 120


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
        self.resize(260, 320)
        self.setMinimumSize(_PALETTE_MIN_WIDTH, _PALETTE_MIN_HEIGHT)

        root = QVBoxLayout(self)
        # 見出し・タブ周囲の余白は最小固定。縦伸長でも増えない。
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(2)

        self._header_wrap = QWidget()
        self._header_wrap.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        header_row = QHBoxLayout(self._header_wrap)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)
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
        self._view_btn.setObjectName("PaletteViewBtn")
        self._view_btn.setToolTip("表示切替")
        self._view_btn.clicked.connect(self._toggle_view)
        self._resize_view_btn()
        header_row.addWidget(self._view_btn)
        root.addWidget(self._header_wrap, 0)

        self._mode_wrap = QWidget()
        self._mode_wrap.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        mode_row = QHBoxLayout(self._mode_wrap)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(2)
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
        root.addWidget(self._mode_wrap, 0)

        self._content_host = QWidget()
        content_lay = QVBoxLayout(self._content_host)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        content_lay.addWidget(self._stack)

        self._draw_page = QWidget()
        draw_lay = QVBoxLayout(self._draw_page)
        draw_lay.setContentsMargins(0, 0, 0, 0)
        draw_lay.setSpacing(4)

        self._brush_frame = QFrame()
        self._brush_frame.setObjectName("FloatingPaletteSection")
        brush_lay = QVBoxLayout(self._brush_frame)
        brush_lay.setContentsMargins(0, 0, 0, 0)
        brush_lay.setSpacing(4)

        colors_row = QHBoxLayout()
        colors_row.setSpacing(4)
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        for i, col in enumerate(PALETTE_COLORS):
            b = QPushButton()
            b.setObjectName("ColorSwatchBtn")
            b.setFixedSize(24, 24)
            b.setCheckable(True)
            b.setProperty("swatchColor", col)
            b.setStyleSheet(
                f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 12px; }}"
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
        tools_row.setSpacing(2)
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
        clear_row.setSpacing(2)
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
        detail_lay.setSpacing(4)

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

        self._text_page = QWidget()
        text_lay = QVBoxLayout(self._text_page)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(4)

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
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self._text_format_scroll.setWidget(self._format_panel)
        text_lay.addWidget(self._text_format_scroll, 0)

        self._phrase_page = QWidget()
        self._phrase_lay = QVBoxLayout(self._phrase_page)
        phrase_lay = self._phrase_lay
        phrase_lay.setContentsMargins(0, 0, 0, 0)
        phrase_lay.setSpacing(4)
        self._phrase_panel = PhrasePalettePanel()
        self._phrase_panel.layout_hint_changed.connect(self._schedule_fit_to_screen)
        self._format_panel.layout_hint_changed.connect(self._schedule_fit_to_screen)
        phrase_lay.addWidget(self._phrase_panel, 1)

        self._phrase_preview = PhraseEditPreviewPanel()
        self._phrase_preview.hide()
        self._phrase_preview.layout_changed.connect(self._schedule_fit_to_screen)
        phrase_lay.addWidget(self._phrase_preview, 0)

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
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self._phrase_format_scroll.setMaximumHeight(16777215)
        self._phrase_format_placeholder = QWidget()
        self._phrase_format_scroll.setWidget(self._phrase_format_placeholder)
        self._phrase_format_scroll.hide()
        phrase_lay.addWidget(self._phrase_format_scroll, 0)

        self._stack.addWidget(self._draw_page)
        self._stack.addWidget(self._text_page)
        self._stack.addWidget(self._phrase_page)

        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._content_scroll.setWidget(self._content_host)
        # stretch=1: 縦伸長の余白は本文領域のみ。見出し/タブ周囲は固定。
        root.addWidget(self._content_scroll, 1)

        self._view_mode = VIEW_SIMPLE
        self._input_mode = MODE_DRAW
        self._palm_rejection = True
        self._draw_tool = TOOL_NONE
        self._current_color = PALETTE_COLORS[0]
        self._pen_btn.setVisible(False)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_to_screen)
        self._apply_view_mode()
        self._apply_palm_rejection_ui()
        self._switch_input_mode(MODE_DRAW, emit=False)
        self._emit_draw_tool(emit=False)

    def _schedule_fit_to_screen(self) -> None:
        self._fit_timer.start()

    def _phrase_edit_active(self) -> bool:
        return (
            self._input_mode == MODE_PHRASE
            and self._phrase_format_scroll.isVisible()
        )

    def _phrase_simple_active(self) -> bool:
        return (
            self._input_mode == MODE_PHRASE
            and self._view_mode == VIEW_SIMPLE
            and not self._phrase_format_scroll.isVisible()
        )

    def _fit_height_to_content(self) -> bool:
        return self._phrase_edit_active() or self._phrase_simple_active()

    def _apply_min_height_policy(self) -> None:
        # 各モードで固定最小高に引っ張られないよう、全体を低い下限に統一。
        self.setMinimumHeight(_PALETTE_MIN_HEIGHT)

    def _phrase_page_content_height(self) -> int:
        lay = self._phrase_lay
        margins = lay.contentsMargins()
        return (
            self._phrase_panel.content_height_hint()
            + margins.top()
            + margins.bottom()
        )

    def _stack_chrome_height(self) -> int:
        root = self.layout()
        if root is None:
            return 100
        margins = root.contentsMargins()
        chrome = margins.top() + margins.bottom()
        gap_count = 0
        for i in range(root.count()):
            item = root.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is self._content_scroll:
                continue
            lay = item.layout()
            if w is not None and w.isVisible():
                chrome += w.sizeHint().height()
                gap_count += 1
            elif lay is not None:
                chrome += lay.sizeHint().height()
                gap_count += 1
        chrome += root.spacing() * max(0, gap_count - 1)
        return chrome

    def _phrase_edit_content_height(self) -> int:
        self._phrase_panel.updateGeometry()
        self._phrase_preview.updateGeometry()
        self._format_panel.updateGeometry()
        lay = self._phrase_lay
        spacing = lay.spacing()
        margins = lay.contentsMargins()
        panel_h = max(
            self._phrase_panel.sizeHint().height(),
            self._phrase_panel.minimumSizeHint().height(),
        )
        preview_h = max(
            self._phrase_preview.sizeHint().height(),
            self._phrase_preview.minimumSizeHint().height(),
        )
        format_h = max(
            self._format_panel.sizeHint().height(),
            self._format_panel.minimumSizeHint().height(),
        )
        return (
            panel_h
            + preview_h
            + format_h
            + margins.top()
            + margins.bottom()
            + 2
        )

    def _content_height_hint(self) -> int:
        page = self._stack.currentWidget()
        if page is None:
            return 200
        if self._phrase_edit_active():
            return self._phrase_edit_content_height()
        if self._phrase_simple_active():
            return self._phrase_page_content_height()
        return max(page.minimumSizeHint().height(), page.sizeHint().height())

    def _apply_phrase_page_stretch(self) -> None:
        if self._input_mode != MODE_PHRASE:
            self._phrase_lay.setStretchFactor(self._phrase_panel, 1)
            return
        # 簡易は内容高に合わせる。詳細はカード固定高のためパネルを伸ばし、余白は一覧末尾へ。
        if self._phrase_format_scroll.isVisible() or self._view_mode == VIEW_SIMPLE:
            self._phrase_lay.setStretchFactor(self._phrase_panel, 0)
        else:
            self._phrase_lay.setStretchFactor(self._phrase_panel, 1)

    def _content_width_hint(self) -> int:
        margins = self.layout().contentsMargins()
        widths = [_PALETTE_MIN_WIDTH]
        if self._input_mode == MODE_PHRASE:
            widths.append(self._phrase_panel.content_min_width())
            if self._phrase_format_scroll.isVisible():
                widths.append(self._format_panel.content_min_width())
        elif self._input_mode == MODE_TEXT:
            widths.append(self._format_panel.content_min_width())
        page = self._stack.currentWidget()
        if page is not None:
            widths.append(page.minimumSizeHint().width())
        inner = max(widths)
        return inner + margins.left() + margins.right() + 4

    def _apply_palette_min_width(self) -> None:
        bounds = self._screen_bounds()
        max_w = bounds[0] if bounds else 16777215
        min_w = max(_PALETTE_MIN_WIDTH, min(self._content_width_hint(), max_w))
        self.setMinimumWidth(min_w)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_to_screen()

    def _screen_bounds(self) -> tuple[int, int] | None:
        screen = self.screen()
        if screen is None:
            return None
        avail = screen.availableGeometry()
        max_w = max(self.minimumWidth(), avail.width() - 16)
        max_h = max(self.minimumHeight(), avail.height() - 32)
        return max_w, max_h

    def _fit_to_screen(self) -> None:
        self._apply_min_height_policy()
        bounds = self._screen_bounds()
        if bounds is None:
            return
        screen = self.screen()
        assert screen is not None
        avail = screen.availableGeometry()
        max_w, max_h = bounds
        geo = self.geometry()
        self.setMaximumWidth(max_w)
        self.setMaximumHeight(max_h)
        chrome = self._stack_chrome_height()
        stack_max = max(100, max_h - chrome)
        content_need = self._content_height_hint()
        format_h = max(
            self._format_panel.sizeHint().height(),
            self._format_panel.minimumSizeHint().height(),
        )
        if self._phrase_edit_active():
            self._phrase_format_scroll.setMinimumHeight(format_h)
        content_h = min(content_need, stack_max)
        if content_need <= stack_max:
            # 収まる場合はスクロール領域を内容高に固定して、全表示にする。
            self._content_scroll.setMinimumHeight(content_h)
            self._content_scroll.setMaximumHeight(content_h)
        else:
            # 画面に収まらない場合のみスクロールを許可。
            self._content_scroll.setMinimumHeight(0)
            self._content_scroll.setMaximumHeight(stack_max)
        desired_h = chrome + content_h
        h = max(self.minimumHeight(), min(desired_h, max_h))
        self._apply_palette_min_width()
        hint_w = min(self._content_width_hint(), max_w)
        min_w = self.minimumWidth()
        w = max(min_w, min(hint_w, max_w))
        if geo.width() != w or geo.height() != h:
            self.resize(w, h)
        x = min(max(geo.x(), avail.left()), max(avail.left(), avail.right() - w + 1))
        y = min(max(geo.y(), avail.top()), max(avail.top(), avail.bottom() - h + 1))
        if geo.x() != x or geo.y() != y:
            self.move(x, y)

    def _clamp_geometry(self) -> None:
        self._fit_to_screen()

    @property
    def format_panel(self) -> FormatPalettePanel:
        return self._format_panel

    @property
    def phrase_panel(self) -> PhrasePalettePanel:
        return self._phrase_panel

    @property
    def phrase_preview(self) -> PhraseEditPreviewPanel:
        return self._phrase_preview

    def phrase_format_focus_widgets(self) -> list[QWidget]:
        return [self._format_panel, self._phrase_format_scroll]

    def text_format_focus_widgets(self) -> list[QWidget]:
        return [self._format_panel, self._text_format_scroll]

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
        self._apply_phrase_page_stretch()
        self._schedule_fit_to_screen()

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
        self._apply_min_height_policy()
        detailed = self._view_mode == VIEW_DETAILED
        self._detail_frame.setVisible(detailed)
        self._format_panel.set_detailed_controls_visible(detailed)
        self._phrase_panel.set_view_mode(self._view_mode)
        self._view_btn.setText("簡易" if detailed else "詳細")
        self._resize_view_btn()
        self._apply_phrase_page_stretch()
        self._schedule_fit_to_screen()

    def _resize_view_btn(self) -> None:
        fm = QFontMetrics(self._view_btn.font())
        frame = self._view_btn.style().pixelMetric(
            QStyle.PixelMetric.PM_DefaultFrameWidth, None, self._view_btn
        )
        text_w = fm.horizontalAdvance(self._view_btn.text())
        self._view_btn.setFixedWidth(max(36, text_w + frame * 2 + 14))

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
        panel = self._format_panel
        if not self._widget_alive(panel):
            return
        if visible:
            if self._text_format_scroll.widget() is panel:
                self._text_format_scroll.takeWidget()
            spare = self._phrase_format_scroll.takeWidget()
            if spare is not None and spare is not panel:
                self._phrase_format_placeholder = spare
                spare.setParent(None)
                spare.hide()
            self._phrase_format_scroll.setWidget(panel)
            self._phrase_format_scroll.show()
        else:
            if self._phrase_format_scroll.widget() is panel:
                self._phrase_format_scroll.takeWidget()
            if not self._widget_alive(self._phrase_format_placeholder):
                self._phrase_format_placeholder = QWidget()
            self._phrase_format_scroll.setWidget(self._phrase_format_placeholder)
            self._phrase_format_scroll.hide()
            if (
                self._input_mode == MODE_TEXT
                and self._text_format_scroll.widget() is not panel
            ):
                self._text_format_scroll.setWidget(panel)
        panel.set_template_edit_mode(visible)
        self._phrase_preview.setVisible(visible)
        self._phrase_panel.set_compact_edit_mode(visible)
        self._apply_phrase_page_stretch()
        if visible:
            format_h = self._format_panel.minimumSizeHint().height()
            self._phrase_format_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._phrase_format_scroll.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
            self._phrase_format_scroll.setMinimumHeight(format_h)
            self._phrase_preview.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
            self._stack.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
        else:
            self._phrase_format_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            self._phrase_format_scroll.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
            self._phrase_format_scroll.setMinimumHeight(0)
            self._stack.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
            )
        self._schedule_fit_to_screen()

    @staticmethod
    def _widget_alive(widget: QWidget | None) -> bool:
        if widget is None:
            return False
        try:
            from shiboken6 import isValid

            return bool(isValid(widget))
        except Exception:
            return True

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
