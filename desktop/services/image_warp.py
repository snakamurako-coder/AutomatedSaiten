"""OpenCV による用紙検出・透視変換（index.html の ImageWarp を移植）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from constants import CELL_PX
from services.image_loader import imwrite_bgr, load_image_bgr

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


def clone_corners(corners: Corners) -> Corners:
    return Corners(tl=corners.tl, tr=corners.tr, br=corners.br, bl=corners.bl)


def corners_from_rect(x1: float, y1: float, x2: float, y2: float) -> Corners:
    left = min(x1, x2)
    right = max(x1, x2)
    top = min(y1, y2)
    bottom = max(y1, y2)
    return Corners(tl=(left, top), tr=(right, top), br=(right, bottom), bl=(left, bottom))


def clamp_corner_point(
    x: float, y: float, img_w: int, img_h: int
) -> tuple[float, float]:
    return (
        max(0.0, min(float(img_w), x)),
        max(0.0, min(float(img_h), y)),
    )


def rotate_corners_around_center(
    corners: Corners,
    img_w: int,
    img_h: int,
    deg: float,
) -> Corners:
    if not deg or abs(deg) < 0.01:
        return clone_corners(corners)
    rad = deg * np.pi / 180.0
    cx, cy = img_w / 2.0, img_h / 2.0
    cos_r, sin_r = float(np.cos(rad)), float(np.sin(rad))

    def rot(px: float, py: float) -> tuple[float, float]:
        dx, dy = px - cx, py - cy
        return (
            cx + dx * cos_r - dy * sin_r,
            cy + dx * sin_r + dy * cos_r,
        )

    return Corners(
        tl=rot(*corners.tl),
        tr=rot(*corners.tr),
        br=rot(*corners.br),
        bl=rot(*corners.bl),
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
    warped = warp_image_from_path(source_path, orientation, thresh_val)
    out = Path(output_path)
    imwrite_bgr(out, warped, quality=85)
    return out


def warp_image_from_path(
    source_path: str | Path,
    orientation: Orientation = "landscape",
    thresh_val: int = 128,
) -> np.ndarray:
    image = load_image_bgr(source_path)
    try:
        corners = detect_paper_corners(image, thresh_val)
    except ValueError:
        h, w = image.shape[:2]
        corners = default_paper_corners(w, h)
    return warp_from_corners(image, corners, orientation)


def warp_image_from_array(
    image_bgr: np.ndarray,
    orientation: Orientation = "landscape",
    thresh_val: int = 128,
) -> np.ndarray:
    try:
        corners = detect_paper_corners(image_bgr, thresh_val)
    except ValueError:
        h, w = image_bgr.shape[:2]
        corners = default_paper_corners(w, h)
    return warp_from_corners(image_bgr, corners, orientation)


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
