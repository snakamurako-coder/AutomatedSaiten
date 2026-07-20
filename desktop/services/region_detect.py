"""クリック位置から記述欄候補の最小外接矩形を検出する。"""

from __future__ import annotations

import cv2
import numpy as np


def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask)
    if xs.size == 0 or ys.size == 0:
        raise ValueError("領域を検出できませんでした。")
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def _validate_size(w: int, h: int, img_w: int, img_h: int, *, min_size: int) -> None:
    if w < min_size or h < min_size:
        raise ValueError("検出した領域が小さすぎます。別の解答欄をクリックしてください。")
    if w * h > img_w * img_h * 0.85:
        raise ValueError("領域が大きすぎます。解答欄の内側をクリックしてください。")


def _flood_region_bbox(gray: np.ndarray, x: int, y: int, *, lo_diff: int) -> tuple[int, int, int, int]:
    """輝度差で区切った連結領域の外接矩形。"""
    h, w = gray.shape[:2]
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    work = gray.copy()
    cv2.floodFill(
        work,
        mask,
        (x, y),
        0,
        loDiff=(lo_diff, lo_diff, lo_diff),
        upDiff=(lo_diff, lo_diff, lo_diff),
        flags=flags,
    )
    region = mask[1:-1, 1:-1] > 0
    return _bbox_from_mask(region)


def _contour_region_bbox(
    blur: np.ndarray,
    x: int,
    y: int,
    *,
    thresh: int,
    min_size: int,
    img_w: int,
    img_h: int,
) -> tuple[int, int, int, int]:
    """枠線を膨張させ、クリック点を含む最小輪郭の外接矩形を返す。"""
    binary = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        15,
        8,
    )
    if thresh != 128:
        _, binary = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY_INV)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best: tuple[float, tuple[int, int, int, int]] | None = None
    for cnt in contours:
        if cv2.pointPolygonTest(cnt, (float(x), float(y)), False) < 0:
            continue
        area = cv2.contourArea(cnt)
        if area < min_size * min_size:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw < min_size or bh < min_size:
            continue
        if bw * bh > img_w * img_h * 0.85:
            continue
        if best is None or area < best[0]:
            best = (area, (bx, by, bw, bh))

    if best is None:
        raise ValueError(
            "解答欄の枠を検出できませんでした。"
            "欄の内側をクリックするか、二値化しきい値を調整してください。"
        )
    return best[1]


def detect_region_at_point(
    image_bgr: np.ndarray,
    x: float,
    y: float,
    *,
    thresh: int = 128,
    min_size: int = 15,
) -> tuple[int, int, int, int]:
    """クリック点を含む連結領域の最小外接矩形 (x, y, w, h) を返す。"""
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("画像が読み込まれていません。")

    img_h, img_w = image_bgr.shape[:2]
    ix = int(max(0, min(img_w - 1, round(x))))
    iy = int(max(0, min(img_h - 1, round(y))))

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    lo_diff = max(8, min(40, int(255 - thresh) // 3 + 10))
    try:
        x0, y0, w, h = _flood_region_bbox(blur, ix, iy, lo_diff=lo_diff)
        _validate_size(w, h, img_w, img_h, min_size=min_size)
        return x0, y0, w, h
    except ValueError:
        pass

    _, light = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY)
    filled = light.copy()
    flood_mask = np.zeros((img_h + 2, img_w + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, (ix, iy), 128)
    try:
        x0, y0, w, h = _bbox_from_mask(filled == 128)
        _validate_size(w, h, img_w, img_h, min_size=min_size)
        return x0, y0, w, h
    except ValueError:
        pass

    x0, y0, w, h = _contour_region_bbox(
        blur, ix, iy, thresh=thresh, min_size=min_size, img_w=img_w, img_h=img_h
    )
    _validate_size(w, h, img_w, img_h, min_size=min_size)
    return x0, y0, w, h
