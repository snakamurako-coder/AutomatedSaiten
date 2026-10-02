"""模範解答の補正画像が、原稿のどこから切り出されたかを保持・逆算する。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from models.database import connect, init_db
from models.test_repo import get_model_answer_source_path, get_test_info
from services.image_loader import imread_bgr, load_image_bgr
from services.image_warp import (
    Corners,
    clone_corners,
    default_paper_corners,
    detect_paper_corners,
    warp_from_corners_to_size,
)

_CROP_KEY = "模範解答切り出し"


@dataclass
class ModelCrop:
    """原稿上の四隅と、それに対応する補正画像の大きさ。"""

    corners: Corners
    source_width: int
    source_height: int
    warp_width: int
    warp_height: int


def save_model_crop(
    test_id: str,
    corners: Corners,
    source_width: int,
    source_height: int,
    warp_width: int,
    warp_height: int,
) -> None:
    """補正を作ったときの切り出し四隅を保存する。"""
    payload = {
        "sourceWidth": int(source_width),
        "sourceHeight": int(source_height),
        "warpWidth": int(warp_width),
        "warpHeight": int(warp_height),
        "tl": [float(corners.tl[0]), float(corners.tl[1])],
        "tr": [float(corners.tr[0]), float(corners.tr[1])],
        "br": [float(corners.br[0]), float(corners.br[1])],
        "bl": [float(corners.bl[0]), float(corners.bl[1])],
    }
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO test_info(test_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(test_id, key) DO UPDATE SET value = excluded.value
            """,
            (test_id, _CROP_KEY, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()


def scale_corners(corners: Corners, sx: float, sy: float) -> Corners:
    def scale(pt: tuple[float, float]) -> tuple[float, float]:
        return (float(pt[0]) * sx, float(pt[1]) * sy)

    return Corners(tl=scale(corners.tl), tr=scale(corners.tr), br=scale(corners.br), bl=scale(corners.bl))


def corners_for_image(crop: ModelCrop, image_width: int, image_height: int) -> Corners:
    """模範解答原稿上の切り出しを、同じ相対位置で対象画像へ写す。"""
    src_w = max(1, int(crop.source_width))
    src_h = max(1, int(crop.source_height))
    sx = float(image_width) / float(src_w)
    sy = float(image_height) / float(src_h)
    return scale_corners(crop.corners, sx, sy)


def scale_box(
    box: dict[str, Any],
    from_width: int,
    from_height: int,
    to_width: int,
    to_height: int,
) -> dict[str, Any]:
    sx = float(to_width) / float(max(1, from_width))
    sy = float(to_height) / float(max(1, from_height))
    return {
        **box,
        "x": float(box.get("x") or 0) * sx,
        "y": float(box.get("y") or 0) * sy,
        "width": float(box.get("width") or 0) * sx,
        "height": float(box.get("height") or 0) * sy,
    }


def get_model_crop(test_id: str) -> ModelCrop:
    """保存済みの切り出し。無ければ補正画像と原稿を突き合わせて逆算する。"""
    info = get_test_info(test_id)
    warped_path = str(info.get("modelAnswerPath") or "").strip()
    source_path = get_model_answer_source_path(test_id)
    if not warped_path:
        raise ValueError("模範解答の補正画像がありません。")
    if not source_path:
        raise ValueError("模範解答の原稿がありません。切り出し位置を逆算できません。")
    warped = imread_bgr(warped_path)
    if warped is None:
        raise ValueError(f"模範解答の補正画像を読み込めません: {warped_path}")
    source = load_image_bgr(source_path)
    warp_h, warp_w = warped.shape[:2]
    src_h, src_w = source.shape[:2]
    stored = _load_stored_crop(test_id)
    if (
        stored is not None
        and stored.warp_width == warp_w
        and stored.warp_height == warp_h
        and stored.source_width == src_w
        and stored.source_height == src_h
    ):
        return stored
    corners = _recover_corners(source, warped)
    crop = ModelCrop(corners, src_w, src_h, warp_w, warp_h)
    save_model_crop(test_id, corners, src_w, src_h, warp_w, warp_h)
    return crop


def _load_stored_crop(test_id: str) -> ModelCrop | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM test_info WHERE test_id = ? AND key = ?",
            (test_id, _CROP_KEY),
        ).fetchone()
    raw = str(row["value"] if row else "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        corners = Corners(
            tl=(float(data["tl"][0]), float(data["tl"][1])),
            tr=(float(data["tr"][0]), float(data["tr"][1])),
            br=(float(data["br"][0]), float(data["br"][1])),
            bl=(float(data["bl"][0]), float(data["bl"][1])),
        )
        return ModelCrop(
            corners=corners,
            source_width=int(data["sourceWidth"]),
            source_height=int(data["sourceHeight"]),
            warp_width=int(data["warpWidth"]),
            warp_height=int(data["warpHeight"]),
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _recover_corners(source_bgr: np.ndarray, warped_bgr: np.ndarray) -> Corners:
    """補正画像が原稿のどの四隅から作られたかを、再ワープの一致度で選ぶ。"""
    src_h, src_w = source_bgr.shape[:2]
    candidates: list[Corners] = []
    for thresh in (96, 128, 160, 192):
        try:
            candidates.append(detect_paper_corners(source_bgr, thresh))
        except ValueError:
            continue
    matched = _corners_from_content(source_bgr, warped_bgr)
    if matched is not None:
        candidates.append(matched)
    if not candidates:
        candidates.append(default_paper_corners(src_w, src_h))
    best = min(candidates, key=lambda c: _warp_mae(source_bgr, c, warped_bgr))
    return clone_corners(best)


def _warp_mae(source_bgr: np.ndarray, corners: Corners, warped_bgr: np.ndarray) -> float:
    warp_h, warp_w = warped_bgr.shape[:2]
    try:
        produced = warp_from_corners_to_size(source_bgr, corners, warp_w, warp_h)
    except cv2.error:
        return 1e9
    left = cv2.cvtColor(produced, cv2.COLOR_BGR2GRAY)
    right = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY)
    return float(np.mean(cv2.absdiff(left, right)))


def _downscale(image_bgr: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    height, width = image_bgr.shape[:2]
    scale = min(1.0, float(max_side) / float(max(width, height, 1)))
    if scale >= 0.999:
        return image_bgr, 1.0
    resized = cv2.resize(
        image_bgr,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _corners_from_content(source_bgr: np.ndarray, warped_bgr: np.ndarray) -> Corners | None:
    """特徴点で、補正画像の四隅が原稿のどこに対応するかを求める。"""
    src_small, src_scale = _downscale(source_bgr, 1400)
    warp_small, warp_scale = _downscale(warped_bgr, 1400)
    src_gray = cv2.cvtColor(src_small, cv2.COLOR_BGR2GRAY)
    warp_gray = cv2.cvtColor(warp_small, cv2.COLOR_BGR2GRAY)
    if not hasattr(cv2, "SIFT_create"):
        return None
    sift = cv2.SIFT_create(nfeatures=4000)
    warp_keys, warp_desc = sift.detectAndCompute(warp_gray, None)
    src_keys, src_desc = sift.detectAndCompute(src_gray, None)
    if warp_desc is None or src_desc is None or len(warp_keys) < 8 or len(src_keys) < 8:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(warp_desc, src_desc, k=2)
    good = []
    for pair in pairs:
        if len(pair) < 2:
            continue
        first, second = pair
        if first.distance < 0.75 * second.distance:
            good.append(first)
    if len(good) < 12:
        return None
    warp_pts = np.float32([warp_keys[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    src_pts = np.float32([src_keys[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography_small, mask = cv2.findHomography(warp_pts, src_pts, cv2.RANSAC, 5.0)
    if homography_small is None or mask is None or int(mask.sum()) < 10:
        return None
    src_s = np.diag([src_scale, src_scale, 1.0])
    warp_s = np.diag([warp_scale, warp_scale, 1.0])
    homography = np.linalg.inv(src_s) @ homography_small @ warp_s
    warp_h, warp_w = warped_bgr.shape[:2]
    rect = np.float32(
        [[0, 0], [warp_w, 0], [warp_w, warp_h], [0, warp_h]]
    ).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(rect, homography).reshape(-1, 2)
    corners = Corners(
        tl=(float(mapped[0][0]), float(mapped[0][1])),
        tr=(float(mapped[1][0]), float(mapped[1][1])),
        br=(float(mapped[2][0]), float(mapped[2][1])),
        bl=(float(mapped[3][0]), float(mapped[3][1])),
    )
    if not _quad_is_usable(corners, source_bgr.shape[1], source_bgr.shape[0]):
        return None
    return corners


def _quad_is_usable(corners: Corners, width: int, height: int) -> bool:
    pts = [corners.tl, corners.tr, corners.br, corners.bl]
    margin_x = width * 0.25
    margin_y = height * 0.25
    for x, y in pts:
        if x < -margin_x or y < -margin_y or x > width + margin_x or y > height + margin_y:
            return False
    area = 0.0
    for i, (x, y) in enumerate(pts):
        nx, ny = pts[(i + 1) % 4]
        area += x * ny - nx * y
    return abs(area) * 0.5 > width * height * 0.05
