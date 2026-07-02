"""OpenCV による用紙検出・透視変換（index.html の ImageWarp を移植）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from constants import CELL_PX

Orientation = Literal["landscape", "portrait"]


@dataclass
class PaperConfig:
    is_portrait: bool
    border_cols: int
    border_rows: int
    warp_w: int
    warp_h: int
    id_start_col: int
    id_start_row: int


@dataclass
class Corners:
    tl: tuple[float, float]
    tr: tuple[float, float]
    br: tuple[float, float]
    bl: tuple[float, float]


def get_paper_config(orientation: Orientation = "landscape") -> PaperConfig:
    is_portrait = orientation == "portrait"
    border_cols = 51 if is_portrait else 73
    border_rows = 73 if is_portrait else 51
    return PaperConfig(
        is_portrait=is_portrait,
        border_cols=border_cols,
        border_rows=border_rows,
        warp_w=border_cols * CELL_PX,
        warp_h=border_rows * CELL_PX,
        id_start_col=42 if is_portrait else 64,
        id_start_row=5,
    )


def default_paper_corners(img_w: int, img_h: int) -> Corners:
    mx = max(8, int(img_w * 0.03))
    my = max(8, int(img_h * 0.03))
    return Corners(
        tl=(mx, my),
        tr=(img_w - mx, my),
        br=(img_w - mx, img_h - my),
        bl=(mx, img_h - my),
    )


def detect_paper_corners(image_bgr: np.ndarray, thresh_val: int = 128) -> Corners:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("用紙外周の黒い太枠線を検知できません。")

    max_contour = max(contours, key=cv2.contourArea)
    epsilon = 0.02 * cv2.arcLength(max_contour, True)
    approx = cv2.approxPolyDP(max_contour, epsilon, True)
    if len(approx) != 4:
        raise ValueError("四角形外枠の検出に失敗しました。")

    pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
    pts.sort(key=lambda p: p[1])
    top = sorted(pts[:2], key=lambda p: p[0])
    bottom = sorted(pts[2:], key=lambda p: p[0], reverse=True)
    return Corners(tl=top[0], tr=top[1], br=bottom[0], bl=bottom[1])


def warp_from_corners(
    image_bgr: np.ndarray,
    corners: Corners,
    orientation: Orientation = "landscape",
) -> np.ndarray:
    cfg = get_paper_config(orientation)
    src = np.float32([corners.tl, corners.tr, corners.br, corners.bl])
    dst = np.float32([[0, 0], [cfg.warp_w, 0], [cfg.warp_w, cfg.warp_h], [0, cfg.warp_h]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image_bgr, matrix, (cfg.warp_w, cfg.warp_h))


def warp_image_file(
    source_path: str | Path,
    output_path: str | Path,
    orientation: Orientation = "landscape",
    thresh_val: int = 128,
) -> Path:
    image = cv2.imread(str(source_path))
    if image is None:
        raise ValueError(f"画像を読み込めません: {source_path}")
    try:
        corners = detect_paper_corners(image, thresh_val)
    except ValueError:
        h, w = image.shape[:2]
        corners = default_paper_corners(w, h)
    warped = warp_from_corners(image, corners, orientation)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), warped, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return out


def crop_region(image_bgr: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    ih, iw = image_bgr.shape[:2]
    x1 = max(0, min(iw, x))
    y1 = max(0, min(ih, y))
    x2 = max(0, min(iw, x + w))
    y2 = max(0, min(ih, y + h))
    if x2 <= x1 or y2 <= y1:
        return image_bgr.copy()
    return image_bgr[y1:y2, x1:x2].copy()


def warped_file_name(original_name: str) -> str:
    stem = Path(original_name).stem
    return f"補正_{stem}.jpg"
