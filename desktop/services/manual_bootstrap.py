"""手動採点ルート — 空DB作成（OCR なしで results 行を用意）。"""

from __future__ import annotations

from typing import Any

from models.test_repo import bootstrap_empty_result_row, get_answer_fields
from services.work_queue import find_warped_for_original


def run_manual_bootstrap(
    test_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """チェックした答案について空テキストの採点用レコードを作成する。"""
    fields = get_answer_fields(test_id)
    field_ids = [str(f["id"]) for f in fields if f.get("id")]
    if not field_ids:
        raise ValueError("記述欄が設定されていません。先に ② 回答欄設定を行ってください。")

    inserted = 0
    updated = 0
    skipped_no_warp: list[str] = []
    errors: list[dict[str, str]] = []

    for item in items:
        file_name = str(item.get("name") or item.get("fileName") or "")
        if not file_name:
            continue
        warped_path = str(item.get("warpedPath") or "").strip()
        if not warped_path:
            found = find_warped_for_original(test_id, file_name)
            warped_path = found or ""
        if not warped_path:
            skipped_no_warp.append(file_name)
            continue
        source_path = str(item.get("path") or item.get("sourcePath") or item.get("id") or "")
        try:
            action = bootstrap_empty_result_row(
                test_id,
                file_name,
                source_path=source_path,
                warped_path=warped_path,
                field_ids=field_ids,
            )
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            else:
                skipped_no_warp.append(file_name)
        except Exception as e:
            errors.append({"fileName": file_name, "error": str(e)})

    return {
        "inserted": inserted,
        "updated": updated,
        "skippedNoWarp": skipped_no_warp,
        "errors": errors,
    }
