"""回答欄画像のクロップ表示。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from config import test_archive, test_inbox
from constants import ORIGINAL_ARCHIVE_FOLDER_NAME
from services.image_loader import imread_bgr, load_image_bgr
from services.image_warp import crop_region


def resolve_warped_path(row: dict[str, Any]) -> str:
    path = str(row.get("warpedPath") or row.get("warped_path") or "").strip()
    if path and Path(path).exists():
        return path
    alt = str(row.get("fileId") or row.get("source_path") or "").strip()
    if alt and Path(alt).exists():
        return alt
    raise FileNotFoundError(f"補正画像が見つかりません: {row.get('fileName', '')}")


def _norm_file_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def _source_file_names(row: dict[str, Any], stored: str) -> list[str]:
    names: list[str] = []
    for raw in (
        Path(stored).name if stored else "",
        str(row.get("fileName") or row.get("file_name") or ""),
    ):
        name = raw.strip()
        if name and name not in names and name not in {".", ".."}:
            names.append(name)
    return names


def _find_named_file(folder: Path, names: list[str]) -> str:
    if not names or not folder.is_dir():
        return ""
    for name in names:
        candidate = folder / name
        if candidate.is_file():
            return str(candidate.resolve())
    wanted = {_norm_file_name(name) for name in names}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return ""
    for entry in entries:
        if entry.is_file() and _norm_file_name(entry.name) in wanted:
            return str(entry.resolve())
    return ""


def resolve_source_path(row: dict[str, Any], *, test_id: str = "") -> str:
    """生徒の元画像。取り込み後は「元画像」フォルダへ移るので、記録パスが無くてもそこを探す。"""
    stored = str(row.get("sourcePath") or row.get("source_path") or "").strip()
    if stored and Path(stored).is_file():
        return stored
    names = _source_file_names(row, stored)
    tid = str(test_id or row.get("testId") or row.get("test_id") or "").strip()
    folders: list[Path] = []
    if tid:
        folders.append(test_archive(tid))
        folders.append(test_inbox(tid))
    if stored:
        parent = Path(stored).parent
        folders.append(parent)
        if parent.name != ORIGINAL_ARCHIVE_FOLDER_NAME:
            folders.append(parent.parent / ORIGINAL_ARCHIVE_FOLDER_NAME)
    warped = str(row.get("warpedPath") or row.get("warped_path") or "").strip()
    if warped:
        warped_dir = Path(warped).parent
        if warped_dir.name == "warped":
            root = warped_dir.parent
            folders.append(root / ORIGINAL_ARCHIVE_FOLDER_NAME)
            folders.append(root / "inbox")
    seen: set[str] = set()
    for folder in folders:
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        found = _find_named_file(folder, names)
        if found:
            return found
    label = row.get("fileName") or (Path(stored).name if stored else "")
    raise FileNotFoundError(f"元画像が見つかりません: {label}")


def crop_field_from_row(row: dict[str, Any], field: dict[str, Any]) -> Image.Image:
    basis = str(field.get("imageBasis") or "warped").strip()
    if basis == "original":
        image_path = resolve_source_path(row, test_id=str(field.get("testId") or ""))
        image = load_image_bgr(image_path)
    else:
        image_path = resolve_warped_path(row)
        image = imread_bgr(image_path)
        if image is None:
            raise ValueError(f"画像を読み込めません: {image_path}")
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
