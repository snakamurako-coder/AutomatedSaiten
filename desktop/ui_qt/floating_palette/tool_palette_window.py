"""描画ツールフローティングパレット。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui_qt.floating_palette.palette_prefs import (
    PALETTE_COLORS,
    TOOL_ERASER,
    TOOL_PEN,
    TOOL_TEXT,
    VIEW_DETAILED,
    VIEW_SIMPLE,
)
from ui_qt.stylus_overlay import ERASER_MODE_PIXEL, ERASER_MODE_STROKE


class _DragHeader(QFrame):
    moved = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setStyleSheet("background: #f3f4f6; border-radius: 4px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        self._grip = QLabel("⋮⋮")
        self._grip.setStyleSheet("border: none; color: #6b7280;")
        lay.addWidget(self._grip)
        lay.addStretch()
        self._origin = QPoint()
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._origin
            self._origin = event.globalPosition().toPoint()
            self.moved.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        event.accept()


class ToolPaletteWindow(QWidget):
    """別ウィンドウ型描画ツールパレット。"""

    tool_changed = Signal(str)
    brush_changed = Signal(str, float, float)
    eraser_mode_changed = Signal(str)
    show_ink_changed = Signal(bool)
    view_mode_changed = Signal(str)
    minimize_requested = Signal()
    drag_moved = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("🎨 ツール")
        self.setObjectName("ToolPaletteWindow")
        self.resize(240, 320)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header_row = QHBoxLayout()
        self._header = _DragHeader()
        self._header.moved.connect(self.drag_moved.emit)
        header_row.addWidget(self._header, 1)
        self._min_btn = QPushButton("−")
        self._min_btn.setFixedSize(24, 24)
        self._min_btn.clicked.connect(self.minimize_requested.emit)
        header_row.addWidget(self._min_btn)
        self._view_btn = QPushButton("詳細")
        self._view_btn.setFixedHeight(24)
        self._view_btn.clicked.connect(self._toggle_view)
        header_row.addWidget(self._view_btn)
        root.addLayout(header_row)

        tools_row = QHBoxLayout()
        self._tool_group = QButtonGroup(self)
        self._pen_btn = QPushButton("ペン")
        self._pen_btn.setCheckable(True)
        self._eraser_btn = QPushButton("消しゴム")
        self._eraser_btn.setCheckable(True)
        self._text_btn = QPushButton("テキスト")
        self._text_btn.setCheckable(True)
        for i, btn in enumerate((self._pen_btn, self._eraser_btn, self._text_btn)):
            self._tool_group.addButton(btn, i)
            tools_row.addWidget(btn)
        self._tool_group.idClicked.connect(self._on_tool_id)
        root.addLayout(tools_row)

        self._text_place_btn = QPushButton("テキストボックスを配置")
        self._text_place_btn.clicked.connect(lambda: self._set_tool(TOOL_TEXT))
        root.addWidget(self._text_place_btn)

        colors_row = QHBoxLayout()
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        for i, col in enumerate(PALETTE_COLORS):
            b = QPushButton()
            b.setFixedSize(24, 24)
            b.setCheckable(True)
            b.setStyleSheet(f"background: {col}; border: 1px solid #ccc; border-radius: 12px;")
            b.clicked.connect(lambda _c=False, c=col: self._pick_color(c))
            self._color_group.addButton(b, i)
            colors_row.addWidget(b)
            self._color_btns.append(b)
        root.addLayout(colors_row)

        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("太さ"))
        self._width_slider = QSlider(Qt.Orientation.Horizontal)
        self._width_slider.setRange(1, 20)
        self._width_slider.setValue(3)
        self._width_slider.valueChanged.connect(self._emit_brush)
        width_row.addWidget(self._width_slider, 1)
        root.addLayout(width_row)

        self._detail_frame = QFrame()
        detail_lay = QVBoxLayout(self._detail_frame)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        alpha_row = QHBoxLayout()
        alpha_row.addWidget(QLabel("透明度"))
        self._alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._alpha_slider.setRange(10, 100)
        self._alpha_slider.setValue(100)
        self._alpha_slider.valueChanged.connect(self._emit_brush)
        alpha_row.addWidget(self._alpha_slider, 1)
        detail_lay.addLayout(alpha_row)
        eraser_row = QHBoxLayout()
        eraser_row.addWidget(QLabel("消しゴム"))
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

        self._view_mode = VIEW_SIMPLE
        self._current_color = PALETTE_COLORS[0]
        self._apply_view_mode()
        self._set_tool(TOOL_PEN)

    def _toggle_view(self) -> None:
        self._view_mode = VIEW_DETAILED if self._view_mode == VIEW_SIMPLE else VIEW_SIMPLE
        self._apply_view_mode()
        self.view_mode_changed.emit(self._view_mode)

    def _apply_view_mode(self) -> None:
        detailed = self._view_mode == VIEW_DETAILED
        self._detail_frame.setVisible(detailed)
        self._view_btn.setText("簡易" if detailed else "詳細")

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()

    def _on_tool_id(self, tool_id: int) -> None:
        tools = (TOOL_PEN, TOOL_ERASER, TOOL_TEXT)
        if 0 <= tool_id < len(tools):
            self._set_tool(tools[tool_id])

    def _set_tool(self, tool: str) -> None:
        mapping = {TOOL_PEN: self._pen_btn, TOOL_ERASER: self._eraser_btn, TOOL_TEXT: self._text_btn}
        btn = mapping.get(tool)
        if btn:
            btn.setChecked(True)
        self.tool_changed.emit(tool)

    def set_tool(self, tool: str) -> None:
        self._set_tool(tool)

    def _pick_color(self, color: str) -> None:
        self._current_color = color
        self._emit_brush()

    def _emit_brush(self) -> None:
        w = float(self._width_slider.value())
        a = float(self._alpha_slider.value()) / 100.0
        self.brush_changed.emit(self._current_color, w, a)

    def set_brush(self, color: str, width: float, alpha: float) -> None:
        self._current_color = color
        self._width_slider.blockSignals(True)
        self._alpha_slider.blockSignals(True)
        self._width_slider.setValue(max(1, min(20, int(round(width)))))
        self._alpha_slider.setValue(max(10, min(100, int(round(alpha * 100)))))
        self._width_slider.blockSignals(False)
        self._alpha_slider.blockSignals(False)
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
            float(self._width_slider.value()),
            float(self._alpha_slider.value()) / 100.0,
        )
