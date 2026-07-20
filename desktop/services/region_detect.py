"""クリック位置から記述欄候補の最小外接矩形を検出する。"""

from __future__ import annotations

import cv2
import numpy as np


def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int]:
    if mask.dtype != np.uint8:
        region = (mask > 0).astype(np.uint8) * 255
    else:
        region = mask
    if not region.any():
        raise ValueError("領域を検出できませんでした。")
    pts = cv2.findNonZero(region)
    if pts is None:
        raise ValueError("領域を検出できませんでした。")
    x, y, w, h = cv2.boundingRect(pts)
    return x, y, w, h


def _validate_size(w: int, h: int, img_w: int, img_h: int, *, min_size: int) -> None:
    if w < min_size or h < min_size:
        raise ValueError("検出した領域が小さすぎます。別の解答欄をクリックしてください。")
    if w * h > img_w * img_h * 0.85:
        raise ValueError("領域が大きすぎます。解答欄の内側をクリックしてください。")


def _default_roi_margin(img_w: int, img_h: int) -> int:
    """クリック周辺だけを切り出して処理する幅（本人欄〜記述欄向け）。"""
    return min(800, max(480, min(img_w, img_h) // 3))


def _roi_bounds(img_w: int, img_h: int, x: int, y: int, margin: int) -> tuple[int, int, int, int]:
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(img_w, x + margin + 1)
    y1 = min(img_h, y + margin + 1)
    return x0, y0, x1, y1


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
    return _bbox_from_mask(mask[1:-1, 1:-1] > 0)


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


def _detect_in_roi(
    blur: np.ndarray,
    ix: int,
    iy: int,
    *,
    thresh: int,
    min_size: int,
    img_w: int,
    img_h: int,
    roi_margin: int,
) -> tuple[int, int, int, int]:
    """クリック周辺 ROI 内で検出し、画像全体座標に戻す。"""
    x0, y0, x1, y1 = _roi_bounds(img_w, img_h, ix, iy, roi_margin)
    blur_roi = blur[y0:y1, x0:x1]
    rx, ry = ix - x0, iy - y0
    roi_w, roi_h = blur_roi.shape[1], blur_roi.shape[0]

    # 枠線付き欄（本人欄・記述欄）向けに輪郭検出を先に試す
    try:
        bx, by, bw, bh = _contour_region_bbox(
            blur_roi, rx, ry, thresh=thresh, min_size=min_size, img_w=img_w, img_h=img_h
        )
        _validate_size(bw, bh, img_w, img_h, min_size=min_size)
        return bx + x0, by + y0, bw, bh
    except ValueError:
        pass

    lo_diff = max(8, min(40, int(255 - thresh) // 3 + 10))
    try:
        bx, by, bw, bh = _flood_region_bbox(blur_roi, rx, ry, lo_diff=lo_diff)
        _validate_size(bw, bh, img_w, img_h, min_size=min_size)
        return bx + x0, by + y0, bw, bh
    except ValueError:
        pass

    _, light = cv2.threshold(blur_roi, thresh, 255, cv2.THRESH_BINARY)
    filled = light.copy()
    flood_mask = np.zeros((roi_h + 2, roi_w + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, (rx, ry), 128)
    bx, by, bw, bh = _bbox_from_mask(filled == 128)
    _validate_size(bw, bh, img_w, img_h, min_size=min_size)
    return bx + x0, by + y0, bw, bh


def prepare_detect_blur(image_bgr: np.ndarray) -> np.ndarray:
    """画像読込時に一度だけ計算するぼかしグレースケール。"""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def detect_region_at_point(
    image_bgr: np.ndarray,
    x: float,
    y: float,
    *,
    thresh: int = 128,
    min_size: int = 15,
    blur: np.ndarray | None = None,
    roi_margin: int | None = None,
) -> tuple[int, int, int, int]:
    """クリック点を含む連結領域の最小外接矩形 (x, y, w, h) を返す。"""
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("画像が読み込まれていません。")

    img_h, img_w = image_bgr.shape[:2]
    ix = int(max(0, min(img_w - 1, round(x))))
    iy = int(max(0, min(img_h - 1, round(y))))

    if blur is None:
        blur = prepare_detect_blur(image_bgr)

    margin = roi_margin if roi_margin is not None else _default_roi_margin(img_w, img_h)
    try:
        return _detect_in_roi(
            blur,
            ix,
            iy,
            thresh=thresh,
            min_size=min_size,
            img_w=img_w,
            img_h=img_h,
            roi_margin=margin,
        )
    except ValueError:
        expanded = min(max(img_w, img_h), max(margin * 2, margin + 400))
        if expanded <= margin:
            raise
        return _detect_in_roi(
            blur,
            ix,
            iy,
            thresh=thresh,
            min_size=min_size,
            img_w=img_w,
            img_h=img_h,
            roi_margin=expanded,
        )
