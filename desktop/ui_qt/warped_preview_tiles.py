"""⑤トリミング — 補正画像のタイル確認ビュー。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_qt.helpers import bgr_to_qpixmap
from ui_qt.layout_helpers import FlowLayout, configure_crop_image_scroll, make_expanding
from ui_qt.style import COLORS

_THUMB_MAX_W = 220
_THUMB_MAX_H = 160


def _load_thumb(path: str) -> QPixmap | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        scale = min(_THUMB_MAX_W / max(1, w), _THUMB_MAX_H / max(1, h), 1.0)
        if scale < 1.0:
            bgr = cv2.resize(
                bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        return bgr_to_qpixmap(bgr)
    except Exception:  # noqa: BLE001
        return None


class WarpedPreviewTile(QFrame):
    """補正画像1枚のタイル。タップで選択切替。"""

    def __init__(
        self,
        *,
        file_name: str,
        warped_path: str,
        status: str,
        checked: bool,
        on_toggle: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.file_name = file_name
        self._on_toggle = on_toggle
        self._checked = checked
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(_THUMB_MAX_W + 16)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img = QLabel()
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setFixedSize(_THUMB_MAX_W, _THUMB_MAX_H)
        self._img.setStyleSheet(
            f"background: {COLORS['bg']}; border: 1px solid {COLORS['border']}; border-radius: 4px;"
        )
        pix = _load_thumb(warped_path)
        if pix is not None and not pix.isNull():
            self._img.setPixmap(pix)
        else:
            self._img.setText("画像なし")
            self._img.setStyleSheet(
                f"background: {COLORS['bg']}; color: {COLORS['text_muted']};"
                f" border: 1px dashed {COLORS['border_strong']}; border-radius: 4px;"
            )
        lay.addWidget(self._img)

        self._check_lbl = QLabel()
        self._check_lbl.setAlignment(Qt.AlignCenter)
        self._check_lbl.setStyleSheet("font-size: 14px; font-weight: 700; border: none;")
        lay.addWidget(self._check_lbl)

        name = QLabel(file_name)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(
            "font-size: 11px; font-weight: 600; border: none; color: #111827;"
        )
        lay.addWidget(name)

        st = QLabel(status or "")
        st.setAlignment(Qt.AlignCenter)
        st.setStyleSheet(
            f"font-size: 10px; border: none; color: {COLORS['text_secondary']};"
        )
        lay.addWidget(st)

        self.set_checked(checked)

    def set_checked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self._check_lbl.setText("☑ 選択中" if self._checked else "☐ タップで選択")
        if self._checked:
            self.setStyleSheet(
                f"QFrame {{ background: {COLORS['selection_soft']};"
                f" border: 2px solid {COLORS['selection']}; border-radius: 8px; }}"
            )
            self._check_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 700; border: none; color: {COLORS['selection']};"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ background: {COLORS['surface']};"
                f" border: 1px solid {COLORS['border_strong']}; border-radius: 8px; }}"
            )
            self._check_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 600; border: none; color: {COLORS['text_muted']};"
            )

    def is_checked(self) -> bool:
        return self._checked

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._on_toggle(self.file_name)
            event.accept()
            return
        super().mousePressEvent(event)


class WarpedPreviewPanel(QWidget):
    """補正画像をタイル状に並べるパネル。"""

    def __init__(
        self,
        *,
        on_tile_toggle: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_tile_toggle = on_tile_toggle
        self._tiles: dict[str, WarpedPreviewTile] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._caption = QLabel(
            "補正プレビュー — OCR前の仕上がりを確認（タップで選択／一覧のチェックと同期）"
        )
        self._caption.setStyleSheet(
            "font-weight: 700; font-size: 12px; color: #374151;"
        )
        self._caption.setWordWrap(True)
        root.addWidget(self._caption)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        configure_crop_image_scroll(self._scroll)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        self._host = QWidget()
        self._flow = FlowLayout(self._host, h_spacing=8, v_spacing=8)
        self._flow.setContentsMargins(8, 8, 8, 8)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 1)
        make_expanding(self)

        self._empty = QLabel(
            "表示できる補正画像がありません。\n"
            "自動トリミングまたは手動補正で warped 画像を作成してください。"
        )
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(f"color: {COLORS['text_muted']}; padding: 32px;")
        self._empty.setWordWrap(True)
        self._empty.hide()
        root.addWidget(self._empty)

    def clear(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._tiles.clear()

    def rebuild(self, entries: list[dict[str, Any]]) -> None:
        """entries: {fileName, warpedPath, status, checked}"""
        self.clear()
        if not entries:
            self._scroll.hide()
            self._empty.show()
            return
        self._empty.hide()
        self._scroll.show()
        for e in entries:
            name = str(e.get("fileName") or "")
            tile = WarpedPreviewTile(
                file_name=name,
                warped_path=str(e.get("warpedPath") or ""),
                status=str(e.get("status") or ""),
                checked=bool(e.get("checked")),
                on_toggle=self._on_tile_toggle,
            )
            self._tiles[name] = tile
            self._flow.addWidget(tile)

    def set_tile_checked(self, file_name: str, checked: bool) -> None:
        tile = self._tiles.get(file_name)
        if tile is not None:
            tile.set_checked(checked)

    def tile_count(self) -> int:
        return len(self._tiles)
