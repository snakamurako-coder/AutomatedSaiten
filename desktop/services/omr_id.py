"""生徒IDマーク欄の OMR 読取（index.html detectOmrId の移植）。"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from constants import CELL_PX
from services.image_warp import Orientation, PaperConfig, get_paper_config

# GAS index.html と同じグリッド原点オフセット
OFFSET_COL = 2
OFFSET_ROW = 2
DEFAULT_FILL_THRESHOLD = 0.35
DEFAULT_ID_DIGITS = 4


def detect_omr_id(
    warped_bgr: np.ndarray,
    orientation: Orientation | str = "landscape",
    *,
    fill_threshold: float = DEFAULT_FILL_THRESHOLD,
    id_digits: int = DEFAULT_ID_DIGITS,
    cfg: PaperConfig | None = None,
) -> str:
    """補正済み画像から生徒ID（塗りつぶしマーク）を読み取る。

    各桁（行）について 0〜9 マスの塗りつぶし率が最大かつ閾値超えの数字を採用。
    判定できない桁は ``?``（GAS 互換）。
    """
    if warped_bgr is None or warped_bgr.size == 0:
        return "?" * id_digits

    paper = cfg or get_paper_config(orientation)  # type: ignore[arg-type]
    ih, iw = warped_bgr.shape[:2]
    # 補正サイズが想定と違う場合は座標をスケール
    scale_x = iw / float(paper.warp_w) if paper.warp_w else 1.0
    scale_y = ih / float(paper.warp_h) if paper.warp_h else 1.0

    if warped_bgr.ndim == 2:
        gray = warped_bgr
    else:
        gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    digits: list[str] = []
    margin = 4
    cell = CELL_PX
    for r in range(id_digits):
        row_index = paper.id_start_row + r
        marked = "?"
        max_fill = 0.0
        for c in range(10):
            x0 = int(round((paper.id_start_col + c - OFFSET_COL) * cell * scale_x))
            y0 = int(round((row_index - OFFSET_ROW) * cell * scale_y))
            cw = max(1, int(round(cell * scale_x)))
            ch = max(1, int(round(cell * scale_y)))
            m = max(1, int(round(margin * min(scale_x, scale_y))))
            x1 = max(0, min(iw, x0 + m))
            y1 = max(0, min(ih, y0 + m))
            x2 = max(0, min(iw, x0 + cw - m))
            y2 = max(0, min(ih, y0 + ch - m))
            if x2 <= x1 or y2 <= y1:
                continue
            roi = thresh[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            fill_ratio = float(cv2.countNonZero(roi)) / float(roi.size)
            if fill_ratio > fill_threshold and fill_ratio > max_fill:
                max_fill = fill_ratio
                marked = str(c)
        digits.append(marked)
    return "".join(digits)


def is_complete_omr_id(student_id: str) -> bool:
    """全桁が数字として読めたか。"""
    s = str(student_id or "").strip()
    return bool(s) and s.isdigit()
