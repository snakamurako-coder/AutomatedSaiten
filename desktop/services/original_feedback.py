"""個票を元画像の上に合成する。判定は補正画像の座標から用紙位置へ戻す。"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from models.identity_repo import (
    IDENTITY_BASIS_ORIGINAL,
    get_identity_coord_basis,
    get_identity_fields,
)
from services.crop_preview import resolve_source_path
from services.feedback_renderer import (
    _draw_centered_text,
    render_feedback_overlay_layer,
)
from services.image_loader import imread_bgr, load_image_bgr
from services.image_warp import default_paper_corners, detect_paper_corners


def render_feedback_on_original(
    test_id: str,
    row: dict[str, Any],
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
) -> Image.Image:
    """元画像に判定・合計欄・氏名を載せた RGB 画像を返す。"""
    warped = imread_bgr(warped_path)
    if warped is None:
        raise ValueError(f"補正画像を読み込めません: {warped_path}")
    warp_h, warp_w = warped.shape[:2]
    source_path = resolve_source_path(row, test_id=test_id)
    original = load_image_bgr(source_path)

    overlay = render_feedback_overlay_layer(
        (warp_w, warp_h),
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

    name = str(row.get("name") or "").strip()
    name_box = _name_field(test_id)
    basis = get_identity_coord_basis(test_id) or ""
    drew_name = False
    if name and name_box is not None and basis != IDENTITY_BASIS_ORIGINAL:
        _draw_name_in_box(overlay, name_box, name)
        drew_name = True

    composited = _project_overlay(original, overlay, warp_w, warp_h)
    if name and name_box is not None and basis == IDENTITY_BASIS_ORIGINAL:
        _draw_name_in_box(composited, name_box, name)
        drew_name = True
    if name and not drew_name:
        _draw_name_banner(composited, name)
    return composited.convert("RGB")


def _name_field(test_id: str) -> dict[str, Any] | None:
    for field in get_identity_fields(test_id):
        if str(field.get("type") or "") == "氏名":
            return field
    return None


def _draw_name_in_box(image: Image.Image, box: dict[str, Any], name: str) -> None:
    x = float(box.get("x") or 0)
    y = float(box.get("y") or 0)
    w = float(box.get("width") or 0)
    h = float(box.get("height") or 0)
    if w < 8 or h < 8:
        _draw_name_banner(image, name)
        return
    draw = ImageDraw.Draw(image)
    fill = (255, 255, 255, 220) if image.mode == "RGBA" else (255, 255, 255)
    draw.rectangle([x, y, x + w, y + h], fill=fill)
    _draw_centered_text(
        draw,
        x + w / 2,
        y + h / 2,
        name,
        (17, 24, 39, 255),
        max(12, int(min(w, h) * 0.55)),
        w * 0.9,
        min_size=10,
    )


def _draw_name_banner(image: Image.Image, name: str) -> None:
    width, height = image.size
    box_h = max(36, int(height * 0.04))
    box_w = max(160, int(width * 0.32))
    margin = max(8, int(min(width, height) * 0.012))
    draw = ImageDraw.Draw(image)
    x, y = margin, margin
    fill = (255, 255, 255, 230) if image.mode == "RGBA" else (255, 255, 255)
    draw.rectangle([x, y, x + box_w, y + box_h], fill=fill)
    _draw_centered_text(
        draw,
        x + box_w / 2,
        y + box_h / 2,
        name,
        (17, 24, 39, 255),
        max(14, int(box_h * 0.62)),
        box_w * 0.9,
        min_size=12,
    )


def _project_overlay(
    original_bgr: np.ndarray,
    overlay: Image.Image,
    warp_w: int,
    warp_h: int,
) -> Image.Image:
    orig_h, orig_w = original_bgr.shape[:2]
    try:
        corners = detect_paper_corners(original_bgr)
    except ValueError:
        corners = default_paper_corners(orig_w, orig_h)
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
