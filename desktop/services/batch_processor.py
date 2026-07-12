"""バッチ OCR 処理。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from config import faint_thresholds_from_config, load_config, test_archive, test_warped
from models.test_repo import (
    flush_result_rows,
    get_answer_fields,
    get_step3_faint,
    get_use_id_mark,
    normalize_file_name,
    save_step3_faint,
)
from services.faint_ink import analyze_warped_fields
from services.image_loader import imread_bgr
from services.image_warp import warp_image_file, warped_file_name
from services.ocr import run_ocr_on_warped_image
from services.omr_id import detect_omr_id
from services.work_queue import build_ocr_work_queue, find_warped_for_original


ProgressCallback = Callable[[int, int, str], None]
DetailProgressCallback = Callable[[dict[str, Any]], None]

STAGE_LABELS: dict[str, str] = {
    "load_src": "原画像読込",
    "warp": "枠検出・補正",
    "omr": "生徒ID（OMR）",
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
    use_id_mark: bool | None = None,
    on_detail: DetailProgressCallback | None = None,
    index: int = 1,
    total: int = 1,
) -> dict[str, Any]:
    fields = get_answer_fields(test_id)
    if not fields:
        raise ValueError("記述欄が設定されていません。")
    if use_id_mark is None:
        use_id_mark = get_use_id_mark(test_id)

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

        warped_bgr = _load_warped_bgr(warped_path)

        student_id = ""
        if use_id_mark:
            stage = "omr"
            _emit_detail(
                on_detail,
                index=index,
                total=total,
                file_name=file_name,
                stage=stage,
                status="processing",
            )
            student_id = detect_omr_id(warped_bgr, orientation)

        stage = "ocr"
        _emit_detail(
            on_detail,
            index=index,
            total=total,
            file_name=file_name,
            stage=stage,
            status="processing",
        )
        text_mapping = run_ocr_on_warped_image(warped_bgr, fields)

        row = {
            "fileName": file_name,
            "sourcePath": source_path,
            "warpedPath": warped_path,
            "studentId": student_id,
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
    use_id_mark = get_use_id_mark(test_id)
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
                use_id_mark=use_id_mark,
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


def run_ocr_for_manual_warp_entries(
    test_id: str,
    entries: list[dict[str, Any]],
    on_progress: ProgressCallback | None = None,
    on_detail: DetailProgressCallback | None = None,
) -> dict[str, Any]:
    """手動補正済み画像に対して OCR のみ実行する。"""
    cfg = load_config()
    orientation = cfg.get("default_orientation", "landscape")
    use_id_mark = get_use_id_mark(test_id)
    total = len(entries)
    pending_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    item_logs: list[dict[str, Any]] = []

    for idx, entry in enumerate(entries, start=1):
        file_name = entry["fileName"]
        item = {
            "name": file_name,
            "path": entry.get("sourcePath") or "",
            "stage": "ocr_only",
            "warpedPath": entry.get("warpedPath") or "",
        }
        if on_progress:
            on_progress(idx, total, file_name)
        try:
            row = process_single_item(
                test_id,
                item,
                orientation=orientation,
                use_id_mark=use_id_mark,
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
        "itemLogs": item_logs,
    }


def run_ocr_preview_for_entries(
    test_id: str,
    entries: list[dict[str, Any]],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """補正画像を OCR して旧テキストと比較用プレビューを返す（DB 非書き込み）。"""
    from models.test_repo import get_result_by_file_name

    fields = get_answer_fields(test_id)
    if not fields:
        raise ValueError("記述欄が設定されていません。")
    use_id_mark = get_use_id_mark(test_id)
    total = len(entries)
    previews: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for idx, entry in enumerate(entries, start=1):
        file_name = str(entry.get("fileName") or entry.get("name") or "")
        if on_progress:
            on_progress(idx, total, file_name)
        warped_path = str(entry.get("warpedPath") or "").strip()
        source_path = str(entry.get("sourcePath") or entry.get("path") or "")
        try:
            if not warped_path:
                found = find_warped_for_original(test_id, file_name)
                warped_path = found or ""
            if not warped_path:
                raise ValueError("補正画像がありません。")
            warped_bgr = _load_warped_bgr(warped_path)
            student_id = ""
            if use_id_mark:
                cfg = load_config()
                orientation = cfg.get("default_orientation", "landscape")
                student_id = detect_omr_id(warped_bgr, orientation)
            new_texts = run_ocr_on_warped_image(warped_bgr, fields)
            existing = get_result_by_file_name(test_id, file_name)
            old_texts = dict((existing or {}).get("textMapping") or {})
            if existing and not student_id:
                student_id = str(existing.get("studentId") or "")
            previews.append(
                {
                    "fileName": file_name,
                    "sourcePath": source_path
                    or str((existing or {}).get("sourcePath") or ""),
                    "warpedPath": warped_path,
                    "studentId": student_id,
                    "oldTexts": old_texts,
                    "newTexts": new_texts,
                    "hasExisting": existing is not None,
                }
            )
        except Exception as e:
            errors.append({"fileName": file_name, "error": str(e)})

    return {"previews": previews, "errors": errors}


def run_faint_precheck(
    test_id: str,
    items: list[dict[str, Any]],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """未OCR答案を補正（必要時）し、薄い字指標ではじいたものを記録する。"""
    fields = get_answer_fields(test_id)
    if not fields:
        raise ValueError("記述欄が設定されていません。")
    cfg = load_config()
    thresholds = faint_thresholds_from_config(cfg)
    if not bool(thresholds.get("enabled", True)):
        return {
            "checked": 0,
            "faint": 0,
            "ok": 0,
            "errors": [],
            "faintFiles": [],
            "disabled": True,
        }

    orientation = cfg.get("default_orientation", "landscape")
    total = len(items)
    faint_map = get_step3_faint(test_id)
    errors: list[dict[str, str]] = []
    ok_count = 0
    faint_count_new = 0

    for idx, item in enumerate(items, start=1):
        file_name = str(item.get("name") or item.get("fileName") or "")
        if on_progress:
            on_progress(idx, total, file_name)
        source_path = item.get("path") or item.get("id") or ""
        key = normalize_file_name(file_name)
        try:
            warped_path = str(item.get("warpedPath") or "").strip()
            if not warped_path:
                found = find_warped_for_original(test_id, file_name)
                warped_path = found or ""
            if not warped_path:
                if not source_path:
                    raise ValueError("原画像パスがありません。")
                out_path = test_warped(test_id) / warped_file_name(file_name)
                warp_image_file(source_path, out_path, orientation=orientation)
                warped_path = str(out_path.resolve())

            warped_bgr = _load_warped_bgr(warped_path)
            analysis = analyze_warped_fields(warped_bgr, fields, thresholds)
            if analysis["isFaint"]:
                worst = analysis.get("worstField") or {}
                faint_map[key] = {
                    "fileName": file_name,
                    "reason": analysis.get("reason") or "",
                    "fieldId": str(worst.get("fieldId") or ""),
                    "metrics": dict(worst.get("metrics") or {}),
                    "failedCriteria": list(worst.get("failedCriteria") or []),
                    "warpedPath": warped_path,
                }
                faint_count_new += 1
            else:
                faint_map.pop(key, None)
                ok_count += 1
        except Exception as e:
            errors.append({"fileName": file_name, "error": str(e)})

    save_step3_faint(test_id, faint_map)
    return {
        "checked": total,
        "faint": faint_count_new,
        "ok": ok_count,
        "errors": errors,
        "faintFiles": [
            v["fileName"]
            for k, v in faint_map.items()
            if normalize_file_name(v.get("fileName") or "")
            in {normalize_file_name(i.get("name") or i.get("fileName") or "") for i in items}
        ],
        "disabled": False,
    }
