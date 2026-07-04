"""回答欄クロップ画像の倍率・メタ情報表示コントロール。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui_qt.style import COLORS

_CROP_ZOOM_SLIDER_STYLE = f"""
QSlider#CropZoomSlider::groove:horizontal {{
    height: 6px;
    background: {COLORS["border"]};
    border-radius: 3px;
}}
QSlider#CropZoomSlider::sub-page:horizontal {{
    background: #7dd3fc;
    border-radius: 3px;
}}
QSlider#CropZoomSlider::add-page:horizontal {{
    background: {COLORS["border"]};
    border-radius: 3px;
}}
QSlider#CropZoomSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: {COLORS["accent"]};
}}
"""


class ZoomControls(QWidget):
    """表示倍率スライダー + 数値入力（つまみ左側は水色）。"""

    def __init__(
        self,
        *,
        min_pct: int = 30,
        max_pct: int = 400,
        value: int = 100,
        slider_max_width: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel("表示倍率"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setObjectName("CropZoomSlider")
        self.zoom_slider.setStyleSheet(_CROP_ZOOM_SLIDER_STYLE)
        self.zoom_slider.setRange(min_pct, max_pct)
        self.zoom_slider.setValue(value)
        if slider_max_width is not None:
            self.zoom_slider.setFixedWidth(slider_max_width)
            lay.addWidget(self.zoom_slider)
        else:
            lay.addWidget(self.zoom_slider, 1)
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(min_pct, max_pct)
        self.zoom_spin.setValue(value)
        self.zoom_spin.setSuffix(" %")
        self.zoom_spin.setFixedWidth(72)
        self.zoom_spin.setKeyboardTracking(True)
        lay.addWidget(self.zoom_spin)
        self.zoom_slider.valueChanged.connect(self._sync_spin_from_slider)
        self.zoom_spin.valueChanged.connect(self._sync_slider_from_spin)

    def zoom_value(self) -> int:
        return self.zoom_slider.value()

    def set_zoom_value(self, value: int) -> None:
        self.zoom_slider.setValue(value)

    def connect_zoom_changed(self, callback: Callable[[], None]) -> None:
        self.zoom_slider.valueChanged.connect(lambda _v: callback())
        self.zoom_spin.valueChanged.connect(lambda _v: callback())

    def _sync_spin_from_slider(self, value: int) -> None:
        if self.zoom_spin.value() != value:
            self.zoom_spin.blockSignals(True)
            self.zoom_spin.setValue(value)
            self.zoom_spin.blockSignals(False)

    def _sync_slider_from_spin(self, value: int) -> None:
        if self.zoom_slider.value() != value:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(value)
            self.zoom_slider.blockSignals(False)


class CropDisplayControls(QWidget):
    """表示倍率スライダー・数値入力・タイルメタ情報の表示切替。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        slider_max_width: int | None = None,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        self._zoom = ZoomControls(
            min_pct=30, max_pct=400, value=100, slider_max_width=slider_max_width
        )
        self.zoom_slider = self._zoom.zoom_slider
        self.zoom_spin = self._zoom.zoom_spin
        root.addWidget(self._zoom)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.show_id_check = QCheckBox("IDを表示")
        self.show_id_check.setChecked(True)
        self.show_file_check = QCheckBox("ファイル名を表示")
        self.show_file_check.setChecked(True)
        self.show_ocr_check = QCheckBox("OCR認識テキストを表示")
        self.show_ocr_check.setChecked(True)
        meta_row.addWidget(self.show_id_check)
        meta_row.addWidget(self.show_file_check)
        meta_row.addWidget(self.show_ocr_check)
        meta_row.addStretch()
        root.addLayout(meta_row)

    def zoom_value(self) -> int:
        return self._zoom.zoom_value()

    def show_id(self) -> bool:
        return self.show_id_check.isChecked()

    def show_file_name(self) -> bool:
        return self.show_file_check.isChecked()

    def show_ocr_text(self) -> bool:
        return self.show_ocr_check.isChecked()

    def connect_zoom_changed(self, callback: Callable[[], None]) -> None:
        self._zoom.connect_zoom_changed(callback)

    def connect_meta_changed(self, callback: Callable[[], None]) -> None:
        self.show_id_check.toggled.connect(lambda _c: callback())
        self.show_file_check.toggled.connect(lambda _c: callback())
        self.show_ocr_check.toggled.connect(lambda _c: callback())
