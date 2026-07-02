"""OCR 処理キュー（code.gs buildOcrWorkQueue_ のローカル版）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config import test_archive, test_inbox, test_warped
from models.test_repo import get_processed_file_names, normalize_file_name


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTENSIONS = {".pdf"}


def natural_compare(a: str, b: str) -> int:
    def parts(s: str) -> list[Any]:
        return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]

    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        return -1 if str(x) < str(y) else 1
    return -1 if len(pa) < len(pb) else (1 if len(pa) > len(pb) else 0)


def list_inbox_files(folder: Path) -> list[dict[str, Any]]:
    if not folder.exists():
        return []
    files = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in IMAGE_EXTENSIONS and ext not in PDF_EXTENSIONS:
            continue
        files.append(
            {
                "id": str(p.resolve()),
                "name": p.name,
                "path": str(p.resolve()),
                "mimeType": "application/pdf" if ext in PDF_EXTENSIONS else "image/jpeg",
                "isPdf": ext in PDF_EXTENSIONS,
                "inArchive": False,
            }
        )
    return files


def find_warped_for_original(test_id: str, original_name: str) -> str | None:
    warped_dir = test_warped(test_id)
    if not warped_dir.exists():
        return None
    stem = Path(original_name).stem
    candidate = warped_dir / f"補正_{stem}.jpg"
    if candidate.exists():
        return str(candidate.resolve())
    for p in warped_dir.glob("補正_*.jpg"):
        if stem in p.stem:
            return str(p.resolve())
    return None


def build_ocr_work_queue(test_id: str, inbox_path: str) -> dict[str, Any]:
    processed = get_processed_file_names(test_id)
    inbox = Path(inbox_path) if inbox_path else test_inbox(test_id)
    archive = test_archive(test_id)

    items_by_name: dict[str, dict[str, Any]] = {}

    def ensure_item(meta: dict[str, Any]) -> None:
        key = normalize_file_name(meta["name"])
        if not key or key in processed:
            return
        if key not in items_by_name:
            items_by_name[key] = {
                "id": meta.get("id") or meta.get("path") or "",
                "name": meta["name"],
                "path": meta.get("path") or meta.get("id") or "",
                "mimeType": meta.get("mimeType", "image/jpeg"),
                "isPdf": bool(meta.get("isPdf")),
                "stage": "warp_and_ocr",
                "warpedPath": "",
                "inArchive": bool(meta.get("inArchive")),
            }
        else:
            cur = items_by_name[key]
            if meta.get("id") and not cur["id"]:
                cur["id"] = meta["id"]
            if meta.get("path") and not cur["path"]:
                cur["path"] = meta["path"]
            if meta.get("inArchive"):
                cur["inArchive"] = True

    for f in list_inbox_files(inbox):
        ensure_item(f)

    if archive.exists():
        for f in list_inbox_files(archive):
            ensure_item({**f, "inArchive": True})

    for key, item in items_by_name.items():
        warped = find_warped_for_original(test_id, item["name"])
        if warped:
            item["stage"] = "ocr_only"
            item["warpedPath"] = warped

    items = sorted(items_by_name.values(), key=lambda x: x["name"])
    ocr_only = sum(1 for i in items if i["stage"] == "ocr_only")
    warp_and_ocr = len(items) - ocr_only
    in_inbox = sum(1 for i in items if not i["inArchive"])

    return {
        "items": items,
        "stats": {
            "pending": len(items),
            "ocrOnly": ocr_only,
            "warpAndOcr": warp_and_ocr,
            "inInbox": in_inbox,
            "inSheet": len(processed),
        },
    }
