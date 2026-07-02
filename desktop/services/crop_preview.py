"""回答欄画像のクロップ表示。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from services.image_warp import crop_region


def resolve_warped_path(row: dict[str, Any]) -> str:
    path = str(row.get("warpedPath") or row.get("warped_path") or "").strip()
    if path and Path(path).exists():
        return path
    alt = str(row.get("fileId") or row.get("source_path") or "").strip()
    if alt and Path(alt).exists():
        return alt
    raise FileNotFoundError(f"補正画像が見つかりません: {row.get('fileName', '')}")


def crop_field_from_row(row: dict[str, Any], field: dict[str, Any]) -> Image.Image:
    warped_path = resolve_warped_path(row)
    image = cv2.imread(warped_path)
    if image is None:
        raise ValueError(f"画像を読み込めません: {warped_path}")
    cropped = crop_region(
        image,
        int(field.get("x") or 0),
        int(field.get("y") or 0),
        int(field.get("width") or 0),
        int(field.get("height") or 0),
    )
    rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def load_crops_for_rows(
    rows: list[dict[str, Any]],
    field: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            pil_image = crop_field_from_row(row, field)
            results.append({"ok": True, "row": row, "pil": pil_image})
        except Exception as e:
            results.append({"ok": False, "row": row, "error": str(e)})
    return results
