"""クリック位置から記述欄候補の最小外接矩形を検出する。"""

from __future__ import annotations

import cv2
import numpy as np


def _clamp_seed(gray: np.ndarray, x: int, y: int, *, thresh: int) -> tuple[int, int]:
    """枠線上クリック時、近傍の明るい画素（欄の内側）へシードを移す。"""
    h, w = gray.shape[:2]
    if gray[y, x] >= thresh:
        return x, y
    for radius in range(1, 16):
        y0 = max(0, y - radius)
        y1 = min(h, y + radius + 1)
        x0 = max(0, x - radius)
        x1 = min(w, x + radius + 1)
        patch = gray[y0:y1, x0:x1]
        ys, xs = np.where(patch >= thresh)
        if xs.size:
            return x0 + int(xs[0]), y0 + int(ys[0])
    raise ValueError("枠線上です。解答欄の内側（白い部分）をクリックしてください。")


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
    sx, sy = _clamp_seed(blur, ix, iy, thresh=thresh)

    # 明るい領域（欄の内側）を flood fill
    _, light = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY)
    filled = light.copy()
    flood_mask = np.zeros((img_h + 2, img_w + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, (sx, sy), 128)
    try:
        x0, y0, w, h = _bbox_from_mask(filled == 128)
        _validate_size(w, h, img_w, img_h, min_size=min_size)
        return x0, y0, w, h
    except ValueError:
        pass

    # フォールバック: クリック点を含む輪郭のうち最小面積
    _, binary = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best: tuple[float, tuple[int, int, int, int]] | None = None
    for cnt in contours:
        if cv2.pointPolygonTest(cnt, (float(ix), float(iy)), False) < 0:
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
