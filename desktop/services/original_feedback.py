"""個票を元画像の上に合成する。判定は補正画像の座標から用紙位置へ戻す。"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image

from services.crop_preview import resolve_source_path
from services.feedback_renderer import render_feedback_overlay_layer
from services.image_loader import load_image_bgr
from services.image_warp import Corners
from services.model_crop import corners_for_image, get_model_crop


def render_feedback_on_original(
    test_id: str,
    row: dict[str, Any],
    warped_path: str,  # 呼び出し側の共通引数。位置合わせには使わない
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
) -> Image.Image:
    """元画像に判定・合計欄を載せた RGB 画像を返す。氏名欄は元画像のまま残す。

    判定の座標は模範解答の補正画像上にある。原稿の切り出し四隅へ戻してから、
    生徒の元画像ではその相対位置に載せる。
    """
    crop = get_model_crop(test_id)
    source_path = resolve_source_path(row, test_id=test_id)
    original = load_image_bgr(source_path)
    orig_h, orig_w = original.shape[:2]
    corners = corners_for_image(crop, orig_w, orig_h)

    overlay = render_feedback_overlay_layer(
        (crop.warp_width, crop.warp_height),
        fields,
        output_slots,
        field_marks,
        totals,
        style,
        supersample=1,
    )
    from services.compositor import render_ink_layer, render_text_annotation_layer

    if text_annotations:
        overlay = Image.alpha_composite(
            overlay,
            render_text_annotation_layer(overlay.size, text_annotations, scale=1.0, supersample=1),
        )
    if ink_strokes:
        overlay = Image.alpha_composite(
            overlay,
            render_ink_layer(overlay.size, ink_strokes, scale=1.0, supersample=1),
        )

    composited = _project_overlay(original, overlay, corners)
    return composited.convert("RGB")


def _project_overlay(
    original_bgr: np.ndarray,
    overlay: Image.Image,
    corners: Corners,
) -> Image.Image:
    orig_h, orig_w = original_bgr.shape[:2]
    warp_w, warp_h = overlay.size
    src = np.float32([[0, 0], [warp_w, 0], [warp_w, warp_h], [0, warp_h]])
    dst = np.float32([corners.tl, corners.tr, corners.br, corners.bl])
    matrix = cv2.getPerspectiveTransform(src, dst)
    overlay_bgra = cv2.cvtColor(np.array(overlay.convert("RGBA")), cv2.COLOR_RGBA2BGRA)
    placed = cv2.warpPerspective(
        overlay_bgra,
        matrix,
        (orig_w, orig_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    base = cv2.cvtColor(original_bgr, cv2.COLOR_BGR2BGRA).astype(np.float32)
    alpha = placed[:, :, 3:4].astype(np.float32) / 255.0
    mixed = base * (1.0 - alpha) + placed.astype(np.float32) * alpha
    rgba = cv2.cvtColor(mixed.astype(np.uint8), cv2.COLOR_BGRA2RGBA)
    return Image.fromarray(rgba)
