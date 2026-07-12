"""薄い字の目視確認・強調補正・OCR ダイアログ。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
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

from services.faint_ink import enhance_bgr
from services.image_loader import imread_bgr, imwrite_bgr
from services.image_warp import warped_file_name
from ui_qt.crop_widgets import ZoomControls
from ui_qt.helpers import bgr_to_qpixmap
from ui_qt.style import COLORS


class _PreviewCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        self._fields: list[dict[str, Any]] = []
        self._highlight_id = ""
        self._scale = 1.0
        self._zoom_pct = 100
        self.setMinimumSize(400, 280)

    def set_image(self, bgr: np.ndarray, fields: list[dict[str, Any]], highlight_id: str = "") -> None:
        self._pixmap = bgr_to_qpixmap(bgr)
        self._fields = list(fields or [])
        self._highlight_id = str(highlight_id or "")
        self._recompute_size()
        self.update()

    def set_zoom_pct(self, pct: int) -> None:
        self._zoom_pct = max(25, min(400, int(pct)))
        self._recompute_size()
        self.update()

    def _recompute_size(self) -> None:
        if self._pixmap is None:
            return
        self._scale = self._zoom_pct / 100.0
        w = max(1, int(self._pixmap.width() * self._scale))
        h = max(1, int(self._pixmap.height() * self._scale))
        self.setFixedSize(w, h)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["sidebar"]))
        if self._pixmap is None:
            painter.setPen(QColor(COLORS["text_secondary"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "画像なし")
            return
        target = self.rect()
        painter.drawPixmap(target, self._pixmap)
        for f in self._fields:
            x = int(float(f.get("x") or 0) * self._scale)
            y = int(float(f.get("y") or 0) * self._scale)
            w = int(float(f.get("width") or 0) * self._scale)
            h = int(float(f.get("height") or 0) * self._scale)
            fid = str(f.get("id") or "")
            hot = fid == self._highlight_id
            pen = QPen(QColor("#f59e0b" if hot else COLORS["accent"]))
            pen.setWidth(3 if hot else 2)
            painter.setPen(pen)
            painter.drawRect(x, y, max(1, w), max(1, h))


class FaintReviewDialog(QDialog):
    """要確認（薄い）答案の目視・強調・OCR。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        queue: list[dict[str, Any]],
        fields: list[dict[str, Any]],
        on_ocr: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("薄い字の目視・強調")
        self.resize(920, 680)
        self._test_id = test_id
        self._queue = list(queue)
        self._fields = list(fields)
        self._on_ocr = on_ocr
        self._index = 0
        self._src_bgr: np.ndarray | None = None
        self._preview_bgr: np.ndarray | None = None
        self._pending_ocr: list[dict[str, Any]] = []
        self._highlight_id = ""
        self._did_flush_ocr = False
        self.finished.connect(self._flush_pending_ocr)

        root = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        root.addWidget(self._title)
        self._reason = QLabel("")
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._reason)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        self._canvas = _PreviewCanvas()
        scroll.setWidget(self._canvas)
        root.addWidget(scroll, 1)

        self._zoom = ZoomControls(min_pct=25, max_pct=400, value=100)
        self._zoom.connect_zoom_changed(
            lambda: self._canvas.set_zoom_pct(self._zoom.zoom_value())
        )
        root.addWidget(self._zoom)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("コントラスト"))
        self._contrast = QSlider(Qt.Horizontal)
        self._contrast.setRange(100, 220)
        self._contrast.setValue(135)
        self._contrast.valueChanged.connect(self._refresh_preview)
        controls.addWidget(self._contrast, 1)
        controls.addWidget(QLabel("CLAHE"))
        self._clahe = QSlider(Qt.Horizontal)
        self._clahe.setRange(0, 80)
        self._clahe.setValue(25)
        self._clahe.valueChanged.connect(self._refresh_preview)
        controls.addWidget(self._clahe, 1)
        preset = QPushButton("薄い字プリセット")
        preset.clicked.connect(self._apply_preset)
        controls.addWidget(preset)
        root.addLayout(controls)

        btns = QHBoxLayout()
        self._prev_btn = QPushButton("← 前へ")
        self._prev_btn.clicked.connect(self._go_prev)
        btns.addWidget(self._prev_btn)
        self._next_btn = QPushButton("次へ →")
        self._next_btn.clicked.connect(self._go_next)
        btns.addWidget(self._next_btn)
        btns.addStretch()
        save_only = QPushButton("強調を保存して次へ")
        save_only.setToolTip("強調画像だけ保存し、OCR は行わない")
        save_only.clicked.connect(self._save_and_advance)
        btns.addWidget(save_only)
        ocr_plain = QPushButton("このまま OCR")
        ocr_plain.setToolTip("強調せず現在の補正画像で OCR（比較画面へ）")
        ocr_plain.clicked.connect(lambda: self._run_ocr(enhance=False))
        btns.addWidget(ocr_plain)
        save_ocr = QPushButton("強調を保存して OCR")
        save_ocr.setObjectName("PrimaryBtn")
        save_ocr.clicked.connect(lambda: self._run_ocr(enhance=True))
        btns.addWidget(save_ocr)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._load_current()

    def _apply_preset(self) -> None:
        self._contrast.blockSignals(True)
        self._clahe.blockSignals(True)
        self._contrast.setValue(145)
        self._clahe.setValue(30)
        self._contrast.blockSignals(False)
        self._clahe.blockSignals(False)
        self._refresh_preview()

    def _current(self) -> dict[str, Any] | None:
        if not self._queue or self._index < 0 or self._index >= len(self._queue):
            return None
        return self._queue[self._index]

    def _go_prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._load_current()

    def _go_next(self) -> None:
        if self._index < len(self._queue) - 1:
            self._index += 1
            self._load_current()

    def _load_current(self) -> None:
        entry = self._current()
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._queue) - 1)
        if entry is None:
            self._title.setText("対象なし")
            self._reason.setText("")
            self._src_bgr = None
            self._preview_bgr = None
            return
        name = str(entry.get("fileName") or "")
        reason = str(entry.get("reason") or (entry.get("faint") or {}).get("reason") or "")
        metrics = entry.get("metrics") or (entry.get("faint") or {}).get("metrics") or {}
        self._title.setText(f"{self._index + 1}/{len(self._queue)}  {name}")
        lines = [reason] if reason else []
        if metrics:
            lines.append(
                f"計測: σ={metrics.get('sigma', '—')} / "
                f"P95−P5={metrics.get('p95_p5', '—')} / "
                f"Δ={metrics.get('bg_delta', '—')}"
            )
        self._reason.setText("\n".join(lines) if lines else "計測理由なし")
        path = str(
            entry.get("warpedPath")
            or (entry.get("faint") or {}).get("warpedPath")
            or ""
        )
        if not path or not Path(path).exists():
            self._src_bgr = None
            self._preview_bgr = None
            self._canvas.set_image(
                np.zeros((120, 200, 3), dtype=np.uint8),
                self._fields,
            )
            base = self._reason.text()
            self._reason.setText(
                (base + "\n" if base else "") + "補正画像が見つかりません。"
            )
            return
        loaded = imread_bgr(path)
        if loaded is None:
            self._src_bgr = None
            self._preview_bgr = None
            base = self._reason.text()
            self._reason.setText(
                (base + "\n" if base else "") + "補正画像を読み込めませんでした。"
            )
            return
        self._src_bgr = loaded
        highlight = str(
            entry.get("fieldId")
            or (entry.get("faint") or {}).get("fieldId")
            or ""
        )
        self._highlight_id = highlight
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._src_bgr is None:
            return
        contrast = self._contrast.value() / 100.0
        clahe = self._clahe.value() / 10.0
        self._preview_bgr = enhance_bgr(
            self._src_bgr, contrast=contrast, brightness=0.0, clahe_clip=clahe
        )
        self._canvas.set_image(
            self._preview_bgr,
            self._fields,
            getattr(self, "_highlight_id", ""),
        )

    def _save_enhanced(self, entry: dict[str, Any]) -> str:
        assert self._preview_bgr is not None
        name = str(entry.get("fileName") or "")
        warped_path = Path(
            str(
                entry.get("warpedPath")
                or (entry.get("faint") or {}).get("warpedPath")
                or ""
            )
        )
        if not warped_path.name:
            from config import test_warped

            warped_path = test_warped(self._test_id) / warped_file_name(name)
        warped_path.parent.mkdir(parents=True, exist_ok=True)
        if warped_path.exists():
            backup = warped_path.with_name(warped_path.stem + "_原" + warped_path.suffix)
            if not backup.exists():
                shutil.copy2(str(warped_path), str(backup))
        imwrite_bgr(warped_path, self._preview_bgr, quality=90)
        return str(warped_path.resolve())

    def did_flush_ocr(self) -> bool:
        return self._did_flush_ocr

    def _save_and_advance(self) -> None:
        entry = self._current()
        if entry is None or self._preview_bgr is None:
            return
        warped = self._save_enhanced(entry)
        entry["warpedPath"] = warped
        self._advance_after_queue()

    def _flush_pending_ocr(self, _result: int = 0) -> None:
        if not self._pending_ocr or not self._on_ocr:
            return
        payload = list(self._pending_ocr)
        self._pending_ocr = []
        self._did_flush_ocr = True
        self._on_ocr(payload)

    def _advance_after_queue(self) -> None:
        if self._index < len(self._queue) - 1:
            self._index += 1
            self._load_current()
        else:
            self.accept()

    def _run_ocr(self, *, enhance: bool) -> None:
        entry = self._current()
        if entry is None:
            return
        if enhance:
            if self._preview_bgr is None:
                return
            warped = self._save_enhanced(entry)
            entry["warpedPath"] = warped
        else:
            warped = str(
                entry.get("warpedPath")
                or (entry.get("faint") or {}).get("warpedPath")
                or ""
            )
            if not warped or not Path(warped).exists():
                return
        self._pending_ocr.append(
            {
                "fileName": str(entry.get("fileName") or ""),
                "sourcePath": str(entry.get("sourcePath") or entry.get("path") or ""),
                "warpedPath": warped,
            }
        )
        self._advance_after_queue()
