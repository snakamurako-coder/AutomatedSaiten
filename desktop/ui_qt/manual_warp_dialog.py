"""手動補正ダイアログ（GAS ManualWarpEditor の PC 版）。"""

from __future__ import annotations

import copy
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from config import load_config, test_warped
from services.image_loader import imwrite_bgr, load_image_bgr
from services.image_warp import (
    Corners,
    Orientation,
    clone_corners,
    corners_from_rect,
    detect_paper_corners,
    rotate_corners_around_center,
    warp_from_corners,
)
from ui_qt.crop_widgets import ZoomControls
from ui_qt.helpers import bgr_to_qpixmap
from ui_qt.style import COLORS

_CORNER_KEYS = ("tl", "tr", "br", "bl")
_CORNER_COLORS = {
    "tl": "#ef4444",
    "tr": "#22c55e",
    "br": "#3b82f6",
    "bl": "#eab308",
}
_CORNER_LABELS = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}


class _SourceCanvas(QWidget):
    """原画像上で四隅をドラッグ指定するキャンバス。"""

    HANDLE_R = 10
    MIN_RECT = 10

    corners_changed = Signal()
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image_bgr: np.ndarray | None = None
        self._pixmap = None
        self._scale = 1.0
        self._zoom_pct = 100
        self._fit_scale = 1.0
        self._base_corners: Corners | None = None
        self._rotation_deg = 0.0
        self._mode = "rect_select"
        self._drag_handle: str | None = None
        self._rect_start: tuple[float, float] | None = None
        self._rect_current: tuple[float, float] | None = None
        self._is_rect_dragging = False
        self._keyboard_corner: str | None = None
        self._blink_on = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(280)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 240)

    def set_zoom_pct(self, pct: int) -> None:
        self._zoom_pct = max(30, min(400, int(pct)))
        self._apply_scale()

    def image_size(self) -> tuple[int, int]:
        if self._image_bgr is None:
            return 0, 0
        h, w = self._image_bgr.shape[:2]
        return w, h

    def set_image(self, image_bgr: np.ndarray) -> None:
        self._image_bgr = image_bgr.copy()
        self._pixmap = bgr_to_qpixmap(self._image_bgr)
        self.enter_rect_select("ドラッグで用紙の範囲を指定（始点=左上・終点=右下）")
        self._apply_scale()

    def enter_rect_select(self, status_msg: str | None = None) -> None:
        self._mode = "rect_select"
        self._base_corners = None
        self._rotation_deg = 0.0
        self._clear_keyboard_corner()
        self._is_rect_dragging = False
        self._rect_start = None
        self._rect_current = None
        self._drag_handle = None
        if status_msg:
            self.status.emit(status_msg)
        self.update()

    def set_base_corners(self, corners: Corners | None, *, inherited: bool = False) -> None:
        if corners is None or self._image_bgr is None:
            self.enter_rect_select("ドラッグで用紙の範囲を指定（始点=左上・終点=右下）")
            return
        w, h = self.image_size()
        c = clone_corners(corners)
        c = Corners(
            tl=(_clamp(c.tl[0], w), _clamp(c.tl[1], h)),
            tr=(_clamp(c.tr[0], w), _clamp(c.tr[1], h)),
            br=(_clamp(c.br[0], w), _clamp(c.br[1], h)),
            bl=(_clamp(c.bl[0], w), _clamp(c.bl[1], h)),
        )
        self._base_corners = c
        self._mode = "corner_edit"
        self._rotation_deg = 0.0
        self._clear_keyboard_corner()
        self.update()
        if inherited:
            self.status.emit(
                "前の画像と同じ四隅位置を引き継ぎました。"
                "必要ならドラッグまたはダブルクリック→方向キーで微調整"
            )
        else:
            self.status.emit(
                "四隅をドラッグして台形補正（赤=左上 緑=右上 青=右下 黄=左下）。"
                "点をダブルクリック→方向キーで1px微調整"
            )

    def set_rotation_deg(self, deg: float) -> None:
        self._rotation_deg = float(deg)
        self.update()
        self.corners_changed.emit()

    def rotation_deg(self) -> float:
        return self._rotation_deg

    def effective_corners(self) -> Corners | None:
        if self._base_corners is None or self._image_bgr is None:
            return None
        w, h = self.image_size()
        return rotate_corners_around_center(self._base_corners, w, h, self._rotation_deg)

    def has_preview_corners(self) -> bool:
        return self._base_corners is not None

    def _apply_scale(self) -> None:
        if self._pixmap is None:
            return
        parent = self.parentWidget()
        avail_w = max(320, (parent.width() - 24) if parent else 520)
        avail_h = 520
        self._fit_scale = min(avail_w / self._pixmap.width(), avail_h / self._pixmap.height(), 1.0)
        self._scale = self._fit_scale * (self._zoom_pct / 100.0)
        w = max(1, int(self._pixmap.width() * self._scale))
        h = max(1, int(self._pixmap.height() * self._scale))
        self.setFixedSize(w, h)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_scale()

    def _to_image(self, pos: QPointF) -> tuple[float, float]:
        return pos.x() / self._scale, pos.y() / self._scale

    def _to_canvas(self, x: float, y: float) -> QPointF:
        return QPointF(x * self._scale, y * self._scale)

    def _clamp_image_point(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.image_size()
        return _clamp(x, w), _clamp(y, h)

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self.update()

    def _clear_keyboard_corner(self) -> None:
        self._keyboard_corner = None
        self._blink_timer.stop()
        self._blink_on = False

    def _select_keyboard_corner(self, key: str) -> None:
        self._keyboard_corner = key
        self.setFocus()
        self._blink_on = True
        if not self._blink_timer.isActive():
            self._blink_timer.start()
        self.status.emit(
            f"{_CORNER_LABELS.get(key, key)}を選択中 — 方向キーで1pxずつ微調整（Escで解除）"
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._pixmap is None:
            painter.fillRect(self.rect(), QColor(COLORS["surface"]))
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "画像なし")
            return

        target = QRectF(0, 0, self.width(), self.height())
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

        if self._is_rect_dragging and self._rect_start and self._rect_current:
            a = self._to_canvas(*self._rect_start)
            b = self._to_canvas(*self._rect_current)
            rect = QRectF(a, b).normalized()
            pen = QPen(QColor("#f59e0b"))
            pen.setWidth(2)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(245, 158, 11, 30))
            painter.drawRect(rect)
            return

        corners = self.effective_corners()
        if not corners:
            return

        pts = [self._to_canvas(*getattr(corners, k)) for k in _CORNER_KEYS]
        pen = QPen(QColor("#06b6d4"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for i, pt in enumerate(pts):
            if i == 0:
                painter.drawLine(pt, pts[-1])
            painter.drawLine(pts[i - 1], pt)

        for key, pt in zip(_CORNER_KEYS, pts, strict=True):
            if key == self._keyboard_corner and not self._blink_on:
                continue
            r = self.HANDLE_R + (3 if key == self._keyboard_corner else 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(_CORNER_COLORS[key]))
            painter.drawEllipse(pt, r, r)
            outline = QPen(QColor("#000000" if key == self._keyboard_corner else "#ffffff"))
            outline.setWidth(3 if key == self._keyboard_corner else 2)
            painter.setPen(outline)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(pt, r, r)

    def _hit_handle(self, mx: float, my: float) -> str | None:
        corners = self.effective_corners()
        if not corners:
            return None
        for key in reversed(_CORNER_KEYS):
            pt = self._to_canvas(*getattr(corners, key))
            if np.hypot(mx - pt.x(), my - pt.y()) <= self.HANDLE_R + 6:
                return key
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        mx, my = event.position().x(), event.position().y()
        if self._mode == "corner_edit" and self._base_corners:
            hit = self._hit_handle(mx, my)
            if hit:
                self._drag_handle = hit
                self._clear_keyboard_corner()
                self.setCursor(Qt.ClosedHandCursor)
                return
            return
        self._clear_keyboard_corner()
        self._is_rect_dragging = True
        self._rect_start = self._clamp_image_point(*self._to_image(QPointF(mx, my)))
        self._rect_current = self._rect_start
        self._drag_handle = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        mx, my = event.position().x(), event.position().y()
        if self._is_rect_dragging:
            self._rect_current = self._clamp_image_point(*self._to_image(QPointF(mx, my)))
            self.update()
            return
        if self._drag_handle and self._base_corners:
            ip = self._clamp_image_point(*self._to_image(QPointF(mx, my)))
            if abs(self._rotation_deg) >= 0.01:
                eff = self.effective_corners()
                if eff:
                    self._base_corners = eff
                    self._rotation_deg = 0.0
            corner = getattr(self._base_corners, self._drag_handle)
            setattr(
                self._base_corners,
                self._drag_handle,
                ip,
            )
            self.update()
            self.corners_changed.emit()
            return
        if self._mode == "corner_edit" and self._base_corners:
            self.setCursor(Qt.OpenHandCursor if self._hit_handle(mx, my) else Qt.CrossCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._is_rect_dragging:
            self._finish_rect_drag()
        self._drag_handle = None
        self.setCursor(Qt.CrossCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._mode != "corner_edit" or not self._base_corners:
            return
        mx, my = event.position().x(), event.position().y()
        hit = self._hit_handle(mx, my)
        if hit:
            event.accept()
            self._select_keyboard_corner(hit)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._clear_keyboard_corner()
            self.update()
            if self._mode == "corner_edit":
                self.status.emit("四隅をドラッグして台形補正。点をダブルクリック→方向キーで1px微調整")
            return
        if not self._keyboard_corner or not self._base_corners:
            super().keyPressEvent(event)
            return
        dx = dy = 0
        if event.key() == Qt.Key_Left:
            dx = -1
        elif event.key() == Qt.Key_Right:
            dx = 1
        elif event.key() == Qt.Key_Up:
            dy = -1
        elif event.key() == Qt.Key_Down:
            dy = 1
        else:
            super().keyPressEvent(event)
            return
        event.accept()
        x, y = getattr(self._base_corners, self._keyboard_corner)
        w, h = self.image_size()
        setattr(
            self._base_corners,
            self._keyboard_corner,
            (_clamp(x + dx, w), _clamp(y + dy, h)),
        )
        self.update()
        self.corners_changed.emit()

    def _finish_rect_drag(self) -> None:
        if not self._rect_start or not self._rect_current:
            self._is_rect_dragging = False
            return
        self._is_rect_dragging = False
        dx = abs(self._rect_current[0] - self._rect_start[0])
        dy = abs(self._rect_current[1] - self._rect_start[1])
        if dx >= self.MIN_RECT and dy >= self.MIN_RECT:
            self._base_corners = corners_from_rect(
                self._rect_start[0],
                self._rect_start[1],
                self._rect_current[0],
                self._rect_current[1],
            )
            self._mode = "corner_edit"
            self._rotation_deg = 0.0
            self._clear_keyboard_corner()
            self.status.emit(
                "四隅をドラッグして台形補正（赤=左上 緑=右上 青=右下 黄=左下）。"
                "点をダブルクリック→方向キーで1px微調整"
            )
            self.corners_changed.emit()
        self._rect_start = None
        self._rect_current = None
        self.update()

    def auto_detect(self, thresh_val: int) -> None:
        if self._image_bgr is None:
            return
        try:
            self._base_corners = detect_paper_corners(self._image_bgr, thresh_val)
            self._mode = "corner_edit"
            self._rotation_deg = 0.0
            self._clear_keyboard_corner()
            self.update()
            self.corners_changed.emit()
            self.status.emit(
                "自動検出しました。四隅をドラッグまたはダブルクリック→方向キーで微調整"
            )
        except ValueError as e:
            self.enter_rect_select(f"自動検出失敗: {e} — ドラッグで範囲を指定してください")


class _PreviewCanvas(QWidget):
    """補正プレビュー表示。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        self._zoom_pct = 100
        self._fit_scale = 1.0
        self.setMinimumSize(320, 240)

    def set_zoom_pct(self, pct: int) -> None:
        self._zoom_pct = max(30, min(400, int(pct)))
        self._apply_scale()

    def set_image(self, image_bgr: np.ndarray | None) -> None:
        if image_bgr is None or image_bgr.size == 0:
            self._pixmap = None
            self.setFixedSize(320, 240)
            self.update()
            return
        self._pixmap = bgr_to_qpixmap(image_bgr)
        self._apply_scale()

    def _apply_scale(self) -> None:
        if self._pixmap is None:
            return
        parent = self.parentWidget()
        avail_w = max(320, (parent.width() - 24) if parent else 520)
        avail_h = 520
        self._fit_scale = min(avail_w / self._pixmap.width(), avail_h / self._pixmap.height(), 1.0)
        scale = self._fit_scale * (self._zoom_pct / 100.0)
        w = max(1, int(self._pixmap.width() * scale))
        h = max(1, int(self._pixmap.height() * scale))
        self.setFixedSize(w, h)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_scale()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._pixmap is None:
            painter.fillRect(self.rect(), QColor(COLORS["surface"]))
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "プレビューなし")
            return
        target = QRectF(0, 0, self.width(), self.height())
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))


def _clamp(v: float, limit: float) -> float:
    return max(0.0, min(float(limit), v))


class ManualWarpDialog(QDialog):
    """単体・連続の手動補正モーダル。"""

    def __init__(
        self,
        parent: QWidget,
        *,
        test_id: str,
        orientation: Orientation | str = "landscape",
        on_saved: Callable[[dict[str, Any]], None] | None = None,
        on_batch_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._test_id = test_id
        self._orientation: Orientation = orientation  # type: ignore[assignment]
        self._on_saved = on_saved
        self._on_batch_done = on_batch_done
        self._file_meta: dict[str, Any] | None = None
        self._image_bgr: np.ndarray | None = None
        self._preview_bgr: np.ndarray | None = None
        self._continuous_mode = False
        self._continuous_queue: list[dict[str, Any]] = []
        self._queue_index = 0
        self._inherited_corners: Corners | None = None
        self._saved_entries: list[dict[str, Any]] = []
        self._busy = False

        self.setWindowTitle("手動補正")
        self.setMinimumSize(980, 680)
        self.resize(1100, 760)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("手動補正")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        header.addWidget(self.title_label, 1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 12px;")
        header.addWidget(self.progress_label)
        root.addLayout(header)

        self.status_label = QLabel("—")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        root.addWidget(self.status_label)

        panels = QHBoxLayout()
        panels.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(QLabel("原画像（四隅指定）"))
        self.source_zoom = ZoomControls(value=100, slider_max_width=160)
        left.addWidget(self.source_zoom)
        self.source_scroll = QScrollArea()
        self.source_scroll.setWidgetResizable(True)
        self.source_canvas = _SourceCanvas()
        self.source_scroll.setWidget(self.source_canvas)
        self.source_scroll.setMinimumHeight(360)
        left.addWidget(self.source_scroll, 1)
        src_ctrl = QHBoxLayout()
        src_ctrl.addWidget(QLabel("二値化閾値"))
        self.thresh_slider = QSlider(Qt.Horizontal)
        self.thresh_slider.setRange(50, 200)
        self.thresh_slider.setValue(128)
        self.thresh_val_label = QLabel("128")
        self.thresh_slider.setFixedWidth(120)
        src_ctrl.addWidget(self.thresh_slider)
        src_ctrl.addWidget(self.thresh_val_label)
        src_ctrl.addSpacing(12)
        src_ctrl.addWidget(QLabel("傾き(°)"))
        self.rotate_slider = QSlider(Qt.Horizontal)
        self.rotate_slider.setRange(-120, 120)  # x10 for 0.5 step
        self.rotate_slider.setValue(0)
        self.rotate_val_label = QLabel("0")
        self.rotate_slider.setFixedWidth(120)
        src_ctrl.addWidget(self.rotate_slider)
        src_ctrl.addWidget(self.rotate_val_label)
        src_ctrl.addStretch()
        left.addLayout(src_ctrl)
        panels.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("補正プレビュー"))
        self.preview_zoom = ZoomControls(value=100, slider_max_width=160)
        right.addWidget(self.preview_zoom)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_canvas = _PreviewCanvas()
        self.preview_scroll.setWidget(self.preview_canvas)
        self.preview_scroll.setMinimumHeight(360)
        right.addWidget(self.preview_scroll, 1)
        panels.addLayout(right, 1)
        root.addLayout(panels, 1)

        tool_row = QHBoxLayout()
        tool_row.addWidget(self._mk_btn("自動枠検出", self._on_auto_detect))
        tool_row.addWidget(self._mk_btn("範囲再指定", self._on_reset_corners))
        tool_row.addWidget(self._mk_btn("プレビュー更新", self._on_update_preview))
        tool_row.addStretch()
        root.addLayout(tool_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.save_btn = QPushButton("保存してOCR再実行")
        self.save_btn.setStyleSheet(
            f"background: {COLORS['accent']}; color: white; font-weight: 700; padding: 8px 16px;"
        )
        self.save_btn.clicked.connect(self._on_save_primary)
        btn_row.addWidget(self.save_btn)
        self.close_btn = QPushButton("閉じる")
        self.close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        self.source_canvas.corners_changed.connect(self._on_corners_changed)
        self.source_canvas.status.connect(self._set_status)
        self.thresh_slider.valueChanged.connect(self._on_thresh_changed)
        self.rotate_slider.valueChanged.connect(self._on_rotate_changed)
        self.source_zoom.connect_zoom_changed(lambda: self._on_source_zoom(self.source_zoom.zoom_value()))
        self.preview_zoom.connect_zoom_changed(lambda: self._on_preview_zoom(self.preview_zoom.zoom_value()))

    @staticmethod
    def _mk_btn(text: str, handler: Callable[[], None]) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(handler)
        return btn

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_thresh_changed(self, val: int) -> None:
        self.thresh_val_label.setText(str(val))

    def _on_rotate_changed(self, val: int) -> None:
        deg = val / 10.0
        self.rotate_val_label.setText(str(deg))
        self.source_canvas.set_rotation_deg(deg)

    def _on_source_zoom(self, pct: int) -> None:
        self.source_canvas.set_zoom_pct(pct)

    def _on_preview_zoom(self, pct: int) -> None:
        self.preview_canvas.set_zoom_pct(pct)

    def _on_corners_changed(self) -> None:
        self._update_preview()

    def _on_auto_detect(self) -> None:
        self.source_canvas.auto_detect(int(self.thresh_slider.value()))
        self._update_preview()

    def _on_reset_corners(self) -> None:
        self.rotate_slider.blockSignals(True)
        self.rotate_slider.setValue(0)
        self.rotate_slider.blockSignals(False)
        self.rotate_val_label.setText("0")
        self.source_canvas.enter_rect_select(
            "ドラッグで用紙の範囲を指定（始点=左上・終点=右下）"
        )
        self._preview_bgr = None
        self.preview_canvas.set_image(None)

    def _on_update_preview(self) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        if self._image_bgr is None:
            self.preview_canvas.set_image(None)
            return
        corners = self.source_canvas.effective_corners()
        if corners is None:
            self._preview_bgr = None
            self.preview_canvas.set_image(None)
            return
        try:
            self._preview_bgr = warp_from_corners(
                self._image_bgr, corners, self._orientation
            )
            self.preview_canvas.set_image(self._preview_bgr)
        except Exception as e:  # noqa: BLE001
            self._preview_bgr = None
            self.preview_canvas.set_image(None)
            self._set_status(f"プレビュー生成失敗: {e}")

    def open_single(self, file_meta: dict[str, Any], *, thresh: int = 128) -> None:
        self._reset_session(continuous=False)
        self._file_meta = file_meta
        self.thresh_slider.setValue(thresh)
        self.thresh_val_label.setText(str(thresh))
        self._load_current_file()

    def open_continuous(self, files: list[dict[str, Any]], *, thresh: int = 128) -> None:
        queue = [f for f in files if f.get("path")]
        if not queue:
            return
        self._reset_session(continuous=True)
        self._continuous_queue = queue
        self._queue_index = 0
        self.thresh_slider.setValue(thresh)
        self.thresh_val_label.setText(str(thresh))
        self._update_save_button()
        self._load_current_file()

    def _reset_session(self, *, continuous: bool) -> None:
        self._continuous_mode = continuous
        self._continuous_queue = []
        self._queue_index = 0
        self._inherited_corners = None
        self._saved_entries = []
        self.progress_label.setText("")
        self.rotate_slider.setValue(0)
        self.rotate_val_label.setText("0")
        self.source_zoom.set_zoom_value(100)
        self.preview_zoom.set_zoom_value(100)

    def _update_save_button(self) -> None:
        if self._continuous_mode:
            is_last = self._queue_index >= len(self._continuous_queue) - 1
            self.save_btn.setText(
                "保存して一括OCR開始" if is_last else "保存・次の画像に移る"
            )
        else:
            self.save_btn.setText("保存してOCR再実行")

    def _update_progress(self) -> None:
        if not self._continuous_mode or not self._continuous_queue:
            self.progress_label.setText("")
            return
        self.progress_label.setText(
            f"連続手動補正 {self._queue_index + 1} / {len(self._continuous_queue)}"
            f"（保存済 {len(self._saved_entries)} 件）"
        )

    def _load_current_file(self) -> None:
        meta = self._file_meta
        if self._continuous_mode:
            if self._queue_index < 0 or self._queue_index >= len(self._continuous_queue):
                return
            meta = self._continuous_queue[self._queue_index]
            self._file_meta = meta
        if not meta:
            return
        name = meta.get("name") or ""
        self.title_label.setText(f"手動補正: {name}")
        self._set_status("画像読込中...")
        self._update_progress()
        self._update_save_button()
        try:
            path = meta.get("path") or meta.get("id") or ""
            self._image_bgr = load_image_bgr(path)
            self.source_canvas.set_image(self._image_bgr)
            self.source_canvas.set_base_corners(
                self._inherited_corners, inherited=self._inherited_corners is not None
            )
            self._update_preview()
            if not self._inherited_corners:
                self._set_status(
                    self.status_label.text()
                    or "ドラッグで用紙の範囲を指定（始点=左上・終点=右下）"
                )
        except Exception as e:  # noqa: BLE001
            self._set_status(f"読込失敗: {e}")

    def _current_corners_snapshot(self) -> Corners | None:
        eff = self.source_canvas.effective_corners()
        return clone_corners(eff) if eff else None

    def _save_warped_image(self) -> tuple[str, np.ndarray]:
        if self._preview_bgr is None or self._file_meta is None:
            raise ValueError("補正プレビューがありません。範囲を指定して四隅を調整してください。")
        from services.image_warp import warped_file_name

        file_name = self._file_meta["name"]
        out_path = test_warped(self._test_id) / warped_file_name(file_name)
        imwrite_bgr(out_path, self._preview_bgr, quality=85)
        return str(out_path.resolve()), self._preview_bgr.copy()

    def _on_save_primary(self) -> None:
        if self._busy:
            return
        if not self.source_canvas.has_preview_corners() or self._preview_bgr is None:
            self._set_status("補正プレビューがありません。範囲を指定して四隅を調整してください。")
            return
        if self._continuous_mode:
            self._save_and_next()
        else:
            self._save_and_ocr()

    def _save_and_ocr(self) -> None:
        if not self._file_meta:
            return
        self._busy = True
        self.save_btn.setEnabled(False)
        self._set_status("保存・OCR実行中...")
        try:
            warped_path, _ = self._save_warped_image()
            corners = self._current_corners_snapshot()
            entry = {
                "fileName": self._file_meta["name"],
                "sourcePath": self._file_meta.get("path") or self._file_meta.get("id") or "",
                "warpedPath": warped_path,
                "corners": corners,
            }
            if self._on_saved:
                self._on_saved(entry)
            self.accept()
        except Exception as e:  # noqa: BLE001
            self._set_status(f"失敗: {e}")
        finally:
            self._busy = False
            self.save_btn.setEnabled(True)

    def _save_and_next(self) -> None:
        if not self._file_meta:
            return
        self._busy = True
        self.save_btn.setEnabled(False)
        try:
            warped_path, _ = self._save_warped_image()
            corners = self._current_corners_snapshot()
            self._inherited_corners = corners
            self._saved_entries.append(
                {
                    "fileName": self._file_meta["name"],
                    "sourcePath": self._file_meta.get("path") or self._file_meta.get("id") or "",
                    "warpedPath": warped_path,
                    "corners": corners,
                }
            )
            if self._queue_index >= len(self._continuous_queue) - 1:
                self._finish_continuous()
                return
            self._queue_index += 1
            self._load_current_file()
        except Exception as e:  # noqa: BLE001
            self._set_status(f"保存失敗: {e}")
        finally:
            self._busy = False
            self.save_btn.setEnabled(True)

    def _finish_continuous(self) -> None:
        if not self._saved_entries:
            self._set_status("保存に成功した画像がありません")
            return
        self._set_status(f"OCR一括実行中...（{len(self._saved_entries)} 件）")
        if self._on_batch_done:
            self._on_batch_done({"entries": copy.deepcopy(self._saved_entries)})
        self.accept()

    def _on_close(self) -> None:
        if (
            self._continuous_mode
            and self._preview_bgr is not None
            and self.source_canvas.has_preview_corners()
            and not any(
                e.get("fileName") == (self._file_meta or {}).get("name")
                for e in self._saved_entries
            )
        ):
            from PySide6.QtWidgets import QMessageBox

            ans = QMessageBox.question(
                self,
                "未保存の補正",
                "未保存の補正があります。閉じますか？\n（保存済み分は保持されます）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        self.reject()


def collect_continuous_manual_warp_queue(
    inventory_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """読込・補正失敗分をファイル名順に返す（GAS collectContinuousManualWarpQueue 相当）。"""
    warp_fail_stages = frozenset({"load_src", "warp", "unknown", ""})
    by_name: dict[str, dict[str, Any]] = {}
    for rd in inventory_rows:
        if rd.get("status") != "失敗":
            continue
        stage = str(rd.get("failStage") or "")
        if stage not in warp_fail_stages:
            continue
        q = rd.get("queueItem")
        if not q or not q.get("path"):
            continue
        key = q["name"]
        by_name[key] = q
    return sorted(by_name.values(), key=lambda x: x["name"])
