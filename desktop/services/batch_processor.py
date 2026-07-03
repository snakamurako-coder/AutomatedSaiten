"""バッチ OCR 処理。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from config import load_config, test_archive, test_warped
from models.test_repo import flush_result_rows, get_answer_fields
from services.image_loader import imread_bgr
from services.image_warp import warp_image_file, warped_file_name
from services.ocr import run_ocr_on_warped_image
from services.work_queue import build_ocr_work_queue


ProgressCallback = Callable[[int, int, str], None]
DetailProgressCallback = Callable[[dict[str, Any]], None]

STAGE_LABELS: dict[str, str] = {
    "load_src": "原画像読込",
    "warp": "枠検出・補正",
    "ocr": "OCRテキスト化",
    "save": "DB保存",
    "archive": "原本退避",
    "done": "完了",
    "unknown": "不明",
}


class BatchItemError(Exception):
    """1ファイル処理中の失敗（停止した工程を保持）。"""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


def _emit_detail(
    cb: DetailProgressCallback | None,
    *,
    index: int,
    total: int,
    file_name: str,
    stage: str,
    status: str,
    error: str = "",
    result: dict[str, Any] | None = None,
) -> None:
    if cb:
        cb(
            {
                "index": index,
                "total": total,
                "fileName": file_name,
                "stage": stage,
                "status": status,
                "error": error,
                "result": result,
            }
        )


def _load_warped_bgr(path: str | Path):
    image = imread_bgr(path)
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
    *,
    on_detail: DetailProgressCallback | None = None,
    index: int = 1,
    total: int = 1,
) -> dict[str, Any]:
    fields = get_answer_fields(test_id)
    if not fields:
        raise ValueError("記述欄が設定されていません。")

    source_path = item.get("path") or item.get("id") or ""
    file_name = item["name"]
    stage = "load_src"

    def fail(exc: Exception) -> None:
        _emit_detail(
            on_detail,
            index=index,
            total=total,
            file_name=file_name,
            stage=stage,
            status="failed",
            error=str(exc),
        )
        if isinstance(exc, BatchItemError):
            raise exc
        raise BatchItemError(stage, str(exc)) from exc

    try:
        _emit_detail(
            on_detail,
            index=index,
            total=total,
            file_name=file_name,
            stage="load_src",
            status="processing",
        )

        warped_path = item.get("warpedPath") or ""
        if item.get("stage") == "warp_and_ocr" or not warped_path:
            stage = "warp"
            _emit_detail(
                on_detail,
                index=index,
                total=total,
                file_name=file_name,
                stage=stage,
                status="processing",
            )
            out_path = test_warped(test_id) / warped_file_name(file_name)
            warp_image_file(source_path, out_path, orientation=orientation)
            warped_path = str(out_path.resolve())

        stage = "ocr"
        _emit_detail(
            on_detail,
            index=index,
            total=total,
            file_name=file_name,
            stage=stage,
            status="processing",
        )
        warped_bgr = _load_warped_bgr(warped_path)
        text_mapping = run_ocr_on_warped_image(warped_bgr, fields)

        row = {
            "fileName": file_name,
            "sourcePath": source_path,
            "warpedPath": warped_path,
            "studentId": "",
            "textMapping": text_mapping,
        }
        _emit_detail(
            on_detail,
            index=index,
            total=total,
            file_name=file_name,
            stage="done",
            status="done",
            result=row,
        )
        return row
    except Exception as e:
        fail(e)
        raise AssertionError("unreachable")  # pragma: no cover


def run_batch_ocr(
    test_id: str,
    inbox_path: str,
    on_progress: ProgressCallback | None = None,
    on_detail: DetailProgressCallback | None = None,
    mode: str = "unprocessed",
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    orientation = cfg.get("default_orientation", "landscape")
    if items is None:
        queue = build_ocr_work_queue(test_id, inbox_path)
        items = queue["items"]
        queue_stats = queue["stats"]
    else:
        queue_stats = {}

    total = len(items)
    pending_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    item_logs: list[dict[str, Any]] = []

    for idx, item in enumerate(items, start=1):
        file_name = item["name"]
        if on_progress:
            on_progress(idx, total, file_name)
        try:
            row = process_single_item(
                test_id,
                item,
                orientation=orientation,
                on_detail=on_detail,
                index=idx,
                total=total,
            )
            pending_rows.append(row)
            item_logs.append({"fileName": file_name, "status": "done", "row": row})
        except BatchItemError as e:
            errors.append({"fileName": file_name, "error": str(e), "stage": e.stage})
            item_logs.append(
                {"fileName": file_name, "status": "failed", "error": str(e), "stage": e.stage}
            )
        except Exception as e:
            errors.append({"fileName": file_name, "error": str(e), "stage": "unknown"})
            item_logs.append({"fileName": file_name, "status": "failed", "error": str(e), "stage": "unknown"})

    if pending_rows and on_detail:
        _emit_detail(
            on_detail,
            index=total,
            total=total,
            file_name="",
            stage="save",
            status="processing",
        )

    flush_result = flush_result_rows(test_id, pending_rows)

    if pending_rows and on_detail:
        _emit_detail(
            on_detail,
            index=total,
            total=total,
            file_name="",
            stage="save",
            status="done",
        )

    archive_dir = test_archive(test_id)
    for row in pending_rows:
        if on_detail:
            _emit_detail(
                on_detail,
                index=total,
                total=total,
                file_name=row.get("fileName") or "",
                stage="archive",
                status="processing",
            )
        _archive_source(row.get("sourcePath", ""), archive_dir)
        if on_detail:
            _emit_detail(
                on_detail,
                index=total,
                total=total,
                file_name=row.get("fileName") or "",
                stage="archive",
                status="done",
            )

    return {
        "processed": len(pending_rows),
        "errors": errors,
        "flush": flush_result,
        "queueStats": queue_stats,
        "itemLogs": item_logs,
    }
