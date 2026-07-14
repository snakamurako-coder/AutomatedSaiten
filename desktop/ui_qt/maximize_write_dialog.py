"""最大化書き込みダイアログ（ズーム／送り／パーム領域）。"""

from __future__ import annotations

import copy
from typing import Any, Callable

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui_qt.crop_widgets import ZoomControls, fit_zoom_pct
from ui_qt.stylus_overlay import CropInkImageStack
from ui_qt.stylus_prefs import (
    FIT_MODE_CONTAIN,
    FIT_MODES,
    PALM_GRABBER_CENTER,
    PALM_GRABBER_LEFT,
    PALM_GRABBER_RIGHT,
    VERTICAL_ALIGN_BOTTOM,
    VERTICAL_ALIGN_CENTER,
    VERTICAL_ALIGN_TOP,
    VERTICAL_ALIGNS,
    load_stylus_prefs,
    save_maximize_write_fit_mode,
    save_maximize_write_palm_grabber_side,
    save_maximize_write_vertical_align,
)
from ui_qt.style import COLORS

_GRABBER_H = 18
_GRABBER_W = 72
_MIN_BLANKET = 80


class PalmBlanket(QWidget):
    """下から画像＋スクロールを覆う半透明パーム領域（上端グラバーで高さ変更）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        grabber_side: str = PALM_GRABBER_LEFT,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._height_px = 160
        self._grabber_side = (
            grabber_side
            if grabber_side
            in (PALM_GRABBER_LEFT, PALM_GRABBER_CENTER, PALM_GRABBER_RIGHT)
            else PALM_GRABBER_LEFT
        )
        self._dragging = False
        self._drag_y0 = 0
        self._height0 = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def grabber_side(self) -> str:
        return self._grabber_side

    def set_grabber_side(self, side: str) -> None:
        s = str(side or PALM_GRABBER_LEFT).strip().lower()
        if s not in (PALM_GRABBER_LEFT, PALM_GRABBER_CENTER, PALM_GRABBER_RIGHT):
            s = PALM_GRABBER_LEFT
        self._grabber_side = s
        self.update()

    def blanket_height(self) -> int:
        return int(self._height_px)

    def set_blanket_height(self, height: int) -> None:
        parent = self.parentWidget()
        max_h = max(_MIN_BLANKET, (parent.height() - 24) if parent else 400)
        self._height_px = max(_MIN_BLANKET, min(max_h, int(height)))
        self.relayout()

    def relayout(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        ph = parent.height()
        pw = parent.width()
        h = min(self._height_px, max(_MIN_BLANKET, ph - 10))
        self._height_px = h
        self.setGeometry(0, max(0, ph - h), pw, h)
        self.raise_()

    def _grabber_rect(self) -> QRect:
        gw, gh = _GRABBER_W, _GRABBER_H
        if self._grabber_side == PALM_GRABBER_CENTER:
            gx = max(0, (self.width() - gw) // 2)
        elif self._grabber_side == PALM_GRABBER_RIGHT:
            gx = max(0, self.width() - gw - 8)
        else:
            gx = 8
        return QRect(gx, 0, gw, gh)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 44, 52, 150))
        gr = self._grabber_rect()
        p.fillRect(gr, QColor(220, 224, 230, 230))
        p.setPen(QColor(90, 96, 110))
        cy = gr.center().y()
        for dy in (-3, 0, 3):
            p.drawLine(gr.left() + 14, cy + dy, gr.right() - 14, cy + dy)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._grabber_rect().contains(
            event.position().toPoint()
        ):
            self._dragging = True
            self._drag_y0 = int(event.globalPosition().y())
            self._height0 = self._height_px
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            dy = self._drag_y0 - int(event.globalPosition().y())
            self.set_blanket_height(self._height0 + dy)
            event.accept()
            return
        if self._grabber_rect().contains(event.position().toPoint()):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        event.accept()


class _CanvasHost(QWidget):
    """スクロール全面＋最前面パーム毛布。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        vertical_align: str = VERTICAL_ALIGN_CENTER,
    ) -> None:
        super().__init__(parent)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        side = load_stylus_prefs().get(
            "maximize_write_palm_grabber_side", PALM_GRABBER_LEFT
        )
        self.blanket = PalmBlanket(self, grabber_side=str(side))
        self.set_vertical_align(vertical_align)

    def set_vertical_align(self, align: str) -> None:
        a = str(align or VERTICAL_ALIGN_CENTER).strip().lower()
        if a not in VERTICAL_ALIGNS:
            a = VERTICAL_ALIGN_CENTER
        h = Qt.AlignmentFlag.AlignHCenter
        if a == VERTICAL_ALIGN_TOP:
            v = Qt.AlignmentFlag.AlignTop
        elif a == VERTICAL_ALIGN_BOTTOM:
            v = Qt.AlignmentFlag.AlignBottom
        else:
            v = Qt.AlignmentFlag.AlignVCenter
        self.scroll.setAlignment(h | v)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.scroll.setGeometry(self.rect())
        self.blanket.relayout()


class MaximizeWriteDialog(QDialog):
    """選択画像を最大化して手書きするモーダル。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        items: list[dict[str, Any]],
        palette_controller: Any,
        on_strokes_changed: Callable[[int, str, list], None] | None = None,
        on_annotations_changed: Callable[[int, str, list], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("最大化書き込み")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        prefs = load_stylus_prefs()
        saved_fit = str(prefs.get("maximize_write_fit_mode") or FIT_MODE_CONTAIN)
        if saved_fit not in FIT_MODES:
            saved_fit = FIT_MODE_CONTAIN
        saved_v_align = str(
            prefs.get("maximize_write_vertical_align") or VERTICAL_ALIGN_CENTER
        )
        if saved_v_align not in VERTICAL_ALIGNS:
            saved_v_align = VERTICAL_ALIGN_CENTER

        self._queue = list(items)
        self._index = 0
        self._palette_controller = palette_controller
        self._on_strokes_changed = on_strokes_changed
        self._on_annotations_changed = on_annotations_changed
        self._stack: CropInkImageStack | None = None
        self._fit_mode: str | None = saved_fit
        self._vertical_align = saved_v_align
        self._suppress_fit_clear = False
        self._initial_layout_done = False

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setToolTip("前の画像")
        self._prev_btn.clicked.connect(self._go_prev)
        toolbar.addWidget(self._prev_btn)

        self._pos_label = QLabel("0 / 0")
        self._pos_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        toolbar.addWidget(self._pos_label)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(36)
        self._next_btn.setToolTip("次の画像")
        self._next_btn.clicked.connect(self._go_next)
        toolbar.addWidget(self._next_btn)

        self._file_label = QLabel("")
        self._file_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        toolbar.addWidget(self._file_label)

        self._v_align_combo = QComboBox()
        self._v_align_combo.setToolTip("表示領域内での画像の縦位置")
        self._v_align_combo.addItem("上寄せ", VERTICAL_ALIGN_TOP)
        self._v_align_combo.addItem("中央", VERTICAL_ALIGN_CENTER)
        self._v_align_combo.addItem("下寄せ", VERTICAL_ALIGN_BOTTOM)
        v_idx = max(0, self._v_align_combo.findData(saved_v_align))
        self._v_align_combo.setCurrentIndex(v_idx)
        self._v_align_combo.currentIndexChanged.connect(self._on_vertical_align_changed)
        toolbar.addWidget(self._v_align_combo)

        toolbar.addStretch(1)

        self._zoom = ZoomControls(
            min_pct=10, max_pct=400, value=100, slider_max_width=120
        )
        self._zoom.connect_zoom_changed(self._on_zoom_changed)
        toolbar.addWidget(self._zoom)

        self._fit_group = QButtonGroup(self)
        self._fit_group.setExclusive(True)
        self._fit_btns: dict[str, QPushButton] = {}
        for key, label, tip in (
            ("width", "幅", "縦は見切れてもよいので、横幅が見切れない最大倍率"),
            ("height", "高さ", "横は見切れてもよいので、高さが見切れない最大倍率"),
            (
                "contain",
                "見切れ無し",
                "どこも見切れない範囲で表示領域に収まる最大倍率",
            ),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c=False, k=key: self._on_fit_mode(k))
            self._fit_group.addButton(btn)
            self._fit_btns[key] = btn
            toolbar.addWidget(btn)
            if key == saved_fit:
                btn.setChecked(True)

        toolbar.addWidget(QLabel("グラバー"))
        self._grabber_combo = QComboBox()
        self._grabber_combo.addItem("左", PALM_GRABBER_LEFT)
        self._grabber_combo.addItem("中央", PALM_GRABBER_CENTER)
        self._grabber_combo.addItem("右", PALM_GRABBER_RIGHT)
        side = str(
            prefs.get("maximize_write_palm_grabber_side") or PALM_GRABBER_LEFT
        )
        idx = max(0, self._grabber_combo.findData(side))
        self._grabber_combo.setCurrentIndex(idx)
        self._grabber_combo.currentIndexChanged.connect(self._on_grabber_side_changed)
        toolbar.addWidget(self._grabber_combo)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        toolbar.addWidget(close_btn)
        root.addLayout(toolbar)

        self._host = _CanvasHost(self, vertical_align=saved_v_align)
        root.addWidget(self._host, 1)
        self._host.blanket.set_grabber_side(
            str(self._grabber_combo.currentData() or PALM_GRABBER_LEFT)
        )

        if self._palette_controller is not None:
            self._palette_controller.bind_full_sheet_dialog(self)
            self._palette_controller.show_palette_for_full_sheet()

        self._update_nav()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._initial_layout_done:
            return
        self._initial_layout_done = True
        self.showMaximized()

        def _apply_initial_fit() -> None:
            if self._fit_mode:
                self._apply_fit_zoom(self._fit_mode)
            elif self._queue:
                self._show_current()

        QTimer.singleShot(0, _apply_initial_fit)

    def ink_stack(self) -> CropInkImageStack | None:
        return self._stack

    def is_draw_mode(self) -> bool:
        return True

    def request_clear_ink(self) -> None:
        if self._stack is None:
            return
        self._stack.clear_ink()

    def request_clear_text_boxes(self) -> None:
        if self._stack is None:
            return
        self._stack.clear_all_text_boxes()

    def _current_item(self) -> dict[str, Any] | None:
        if not self._queue or self._index < 0 or self._index >= len(self._queue):
            return None
        return self._queue[self._index]

    def _persist_current(self) -> None:
        if self._stack is None:
            return
        item = self._current_item()
        if item is None:
            return
        strokes = self._stack.ink_overlay.strokes()
        annotations = self._stack.text_layer.annotations()
        item["ink_strokes"] = copy.deepcopy(strokes)
        item["text_annotations"] = copy.deepcopy(annotations)
        rid = int(item.get("result_id") or 0)
        fid = str(item.get("field_id") or "")
        if self._on_strokes_changed is not None:
            self._on_strokes_changed(rid, fid, list(strokes))
        if self._on_annotations_changed is not None:
            self._on_annotations_changed(rid, fid, list(annotations))

    def _go_prev(self) -> None:
        if self._index <= 0:
            return
        self._persist_current()
        self._index -= 1
        self._show_current()

    def _go_next(self) -> None:
        if self._index >= len(self._queue) - 1:
            return
        self._persist_current()
        self._index += 1
        self._show_current()

    def _update_nav(self) -> None:
        n = len(self._queue)
        self._pos_label.setText(f"{self._index + 1} / {n}" if n else "0 / 0")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < n - 1)
        item = self._current_item()
        name = str((item or {}).get("file_name") or "")
        sid = str((item or {}).get("student_id") or "")
        tip_parts = [p for p in (name, f"ID:{sid}" if sid else "") if p]
        self._file_label.setText(name or (f"ID:{sid}" if sid else ""))
        self._file_label.setToolTip(" / ".join(tip_parts))

    def _viewport_size(self) -> tuple[int, int]:
        vp = self._host.scroll.viewport().size()
        return max(40, vp.width() - 8), max(40, vp.height() - 8)

    def _native_image_size(self) -> tuple[int, int]:
        item = self._current_item()
        if item is None:
            return 1, 1
        pil = item.get("pil")
        if pil is None:
            return 1, 1
        return max(1, int(pil.width)), max(1, int(pil.height))

    def _on_fit_mode(self, mode: str) -> None:
        self._fit_mode = mode
        for k, btn in self._fit_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == mode)
            btn.blockSignals(False)
        save_maximize_write_fit_mode(mode)
        self._apply_fit_zoom(mode)

    def _apply_fit_zoom(self, mode: str) -> None:
        nw, nh = self._native_image_size()
        vw, vh = self._viewport_size()
        pct = fit_zoom_pct(nw, nh, vw, vh, mode)
        self._suppress_fit_clear = True
        try:
            self._zoom.set_zoom_value(pct)
        finally:
            self._suppress_fit_clear = False
        self._rebuild_stack()

    def _on_vertical_align_changed(self, _index: int) -> None:
        align = str(self._v_align_combo.currentData() or VERTICAL_ALIGN_CENTER)
        self._vertical_align = align
        self._host.set_vertical_align(align)
        save_maximize_write_vertical_align(align)
        # 配置変更を即反映（スタックサイズはそのまま）
        if self._stack is not None:
            self._host.scroll.setWidget(self._stack)

    def _on_zoom_changed(self) -> None:
        if self._suppress_fit_clear:
            return
        if self._fit_mode is not None:
            self._fit_mode = None
            for btn in self._fit_btns.values():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._rebuild_stack()

    def _on_grabber_side_changed(self, _index: int) -> None:
        side = str(self._grabber_combo.currentData() or PALM_GRABBER_LEFT)
        self._host.blanket.set_grabber_side(side)
        save_maximize_write_palm_grabber_side(side)

    def _show_current(self) -> None:
        self._update_nav()
        if not self._queue:
            if self._palette_controller is not None:
                self._palette_controller.set_full_sheet_stack(None)
            self._host.scroll.setWidget(QWidget())
            self._stack = None
            return
        if self._fit_mode:
            self._apply_fit_zoom(self._fit_mode)
        else:
            self._rebuild_stack()

    def _rebuild_stack(self) -> None:
        item = self._current_item()
        if item is None:
            return

        rid = int(item.get("result_id") or 0)
        fid = str(item.get("field_id") or "")
        prev_rid = (
            int(getattr(self._stack, "result_id", -1) or -1) if self._stack else -1
        )
        snap = None
        if self._stack is not None and prev_rid == rid:
            snap = self._stack.snapshot()

        if self._palette_controller is not None:
            self._palette_controller.set_full_sheet_stack(None)

        pil = item["pil"]
        zoom = max(0.1, min(4.0, self._zoom.zoom_value() / 100.0))
        strokes = list(item.get("ink_strokes") or [])
        sheet_strokes = list(item.get("sheet_ink_strokes") or [])
        annotations = list(item.get("text_annotations") or [])
        if snap is not None:
            strokes = list(snap.get("strokes") or strokes)
            annotations = list(snap.get("annotations") or annotations)

        placement_meta = {
            "resultId": rid,
            "fieldId": fid,
            "studentId": item.get("student_id"),
            "studentName": str(item.get("student_name") or ""),
        }

        self._stack = CropInkImageStack(
            pil_image=pil,
            field_id=fid,
            result_id=rid,
            strokes=strokes,
            sheet_strokes=sheet_strokes,
            annotations=annotations,
            zoom=zoom,
            placement_meta=placement_meta,
            on_strokes_changed=lambda s, r=rid, f=fid: self._emit_strokes(r, f, s),
            on_annotations_changed=lambda s, r=rid, f=fid: self._emit_annotations(
                r, f, s
            ),
        )
        self._stack.set_drawing_enabled(True)
        self._host.scroll.setWidget(self._stack)

        if self._palette_controller is not None:
            self._palette_controller.set_full_sheet_stack(self._stack)
            self._palette_controller.show_palette_for_full_sheet()

        self._host.blanket.relayout()

    def _emit_strokes(self, result_id: int, field_id: str, strokes: list) -> None:
        item = self._current_item()
        if item is not None and int(item.get("result_id") or 0) == int(result_id):
            item["ink_strokes"] = list(strokes)
        if self._on_strokes_changed is not None:
            self._on_strokes_changed(result_id, field_id, list(strokes))

    def _emit_annotations(self, result_id: int, field_id: str, items: list) -> None:
        item = self._current_item()
        if item is not None and int(item.get("result_id") or 0) == int(result_id):
            item["text_annotations"] = list(items)
        if self._on_annotations_changed is not None:
            self._on_annotations_changed(result_id, field_id, list(items))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self._apply_fit_zoom(self._fit_mode)
        else:
            self._host.blanket.relayout()

    def accept(self) -> None:
        self._persist_current()
        super().accept()

    def reject(self) -> None:
        self._persist_current()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._persist_current()
        super().closeEvent(event)
