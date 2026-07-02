"""バッチ OCR 処理。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import cv2

from config import load_config, test_archive, test_warped
from models.test_repo import flush_result_rows, get_answer_fields
from services.image_warp import warp_image_file, warped_file_name
from services.ocr import run_ocr_on_warped_image
from services.work_queue import build_ocr_work_queue


ProgressCallback = Callable[[int, int, str], None]


def _load_warped_bgr(path: str | Path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"補正画像を読み込めません: {path}")
    return image


def _archive_source(source_path: str, archive_dir: Path) -> None:
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / src.name
    if dest.exists():
        return
    shutil.move(str(src), str(dest))


def process_single_item(
    test_id: str,
    item: dict[str, Any],
    orientation: str = "landscape",
) -> dict[str, Any]:
    fields = get_answer_fields(test_id)
    if not fields:
        raise ValueError("記述欄が設定されていません。")

    source_path = item.get("path") or item.get("id") or ""
    file_name = item["name"]
    if item.get("isPdf"):
        raise ValueError(f"PDF は未対応です（先に画像に変換してください）: {file_name}")

    warped_path = item.get("warpedPath") or ""
    if item.get("stage") == "warp_and_ocr" or not warped_path:
        out_path = test_warped(test_id) / warped_file_name(file_name)
        warp_image_file(source_path, out_path, orientation=orientation)
        warped_path = str(out_path.resolve())

    warped_bgr = _load_warped_bgr(warped_path)
    text_mapping = run_ocr_on_warped_image(warped_bgr, fields)

    return {
        "fileName": file_name,
        "sourcePath": source_path,
        "warpedPath": warped_path,
        "studentId": "",
        "textMapping": text_mapping,
    }


def run_batch_ocr(
    test_id: str,
    inbox_path: str,
    on_progress: ProgressCallback | None = None,
    mode: str = "unprocessed",
) -> dict[str, Any]:
    cfg = load_config()
    orientation = cfg.get("default_orientation", "landscape")
    queue = build_ocr_work_queue(test_id, inbox_path)
    items = queue["items"]
    if mode == "retry":
        # 将来: 失敗ログから再実行。現状は全 pending と同じ。
        pass

    total = len(items)
    pending_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for idx, item in enumerate(items, start=1):
        if on_progress:
            on_progress(idx, total, item["name"])
        try:
            row = process_single_item(test_id, item, orientation=orientation)
            pending_rows.append(row)
        except Exception as e:
            errors.append({"fileName": item["name"], "error": str(e)})

    flush_result = flush_result_rows(test_id, pending_rows)
    archive_dir = test_archive(test_id)
    for row in pending_rows:
        _archive_source(row.get("sourcePath", ""), archive_dir)

    return {
        "processed": len(pending_rows),
        "errors": errors,
        "flush": flush_result,
        "queueStats": queue["stats"],
    }
