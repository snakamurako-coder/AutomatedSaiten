"""OCR 処理キュー（code.gs buildOcrWorkQueue_ のローカル版）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config import test_archive, test_inbox, test_warped
from models.test_repo import get_processed_file_names, normalize_file_name
from services.image_loader import ALL_INPUT_EXTENSIONS, PDF_EXTENSIONS


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
        if ext not in ALL_INPUT_EXTENSIONS:
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


def build_file_inventory(test_id: str, inbox_path: str) -> dict[str, Any]:
    """フォルダ・DB・失敗記録を統合したファイル一覧（③ UI 用）。"""
    from models.test_repo import get_result_preview, get_step3_failed, get_step3_faint

    inbox = Path(inbox_path) if inbox_path else test_inbox(test_id)
    archive = test_archive(test_id)
    results = {normalize_file_name(r["fileName"]): r for r in get_result_preview(test_id)}
    failed_map = get_step3_failed(test_id)
    faint_map = get_step3_faint(test_id)

    files_meta: dict[str, dict[str, Any]] = {}

    def add_file(meta: dict[str, Any], in_archive: bool) -> None:
        key = normalize_file_name(meta["name"])
        if not key:
            return
        cur = files_meta.get(key, {})
        cur.update(
            {
                "name": meta["name"],
                "path": meta.get("path") or meta.get("id") or "",
                "id": meta.get("id") or meta.get("path") or "",
                "isPdf": bool(meta.get("isPdf")),
                "inArchive": in_archive or cur.get("inArchive", False),
            }
        )
        files_meta[key] = cur

    for f in list_inbox_files(inbox):
        add_file(f, False)
    if archive.exists():
        for f in list_inbox_files(archive):
            add_file(f, True)

    all_keys = set(files_meta.keys()) | set(results.keys()) | set(failed_map.keys()) | set(
        faint_map.keys()
    )
    rows: list[dict[str, Any]] = []

    for key in sorted(all_keys, key=lambda k: files_meta.get(k, {}).get("name", k)):
        fmeta = files_meta.get(key, {})
        result = results.get(key)
        fail = failed_map.get(key)
        faint = faint_map.get(key)
        name = (
            fmeta.get("name")
            or (result or {}).get("fileName")
            or (fail or {}).get("fileName")
            or (faint or {}).get("fileName")
            or key
        )
        warped = find_warped_for_original(test_id, name)
        in_db = key in results

        if in_db:
            status = "反映済"
            fail_text = ""
            fail_stage = ""
        elif key in failed_map:
            status = "失敗"
            fail_text = (fail or {}).get("error", "")
            fail_stage = (fail or {}).get("stage", "")
        elif key in faint_map and not in_db:
            status = "要確認（薄い）"
            fail_text = (faint or {}).get("reason", "")
            fail_stage = "faint"
        elif warped and fmeta:
            status = "補正済"
            fail_text = ""
            fail_stage = ""
        elif fmeta:
            status = "未処理"
            fail_text = ""
            fail_stage = ""
        else:
            status = "反映済" if in_db else "不明"
            fail_text = ""
            fail_stage = ""

        queue_item: dict[str, Any] | None = None
        if not in_db and fmeta.get("path"):
            queue_item = {
                "id": fmeta.get("id") or fmeta.get("path") or "",
                "name": name,
                "path": fmeta.get("path") or "",
                "mimeType": "application/pdf" if fmeta.get("isPdf") else "image/jpeg",
                "isPdf": bool(fmeta.get("isPdf")),
                "stage": "ocr_only" if warped else "warp_and_ocr",
                "warpedPath": warped or "",
                "inArchive": bool(fmeta.get("inArchive")),
            }

        rows.append(
            {
                "fileName": name,
                "status": status,
                "fail": fail_text,
                "failStage": fail_stage,
                "studentId": (result or {}).get("studentId") or "",
                "texts": (result or {}).get("textMapping") or {},
                "db": "済" if in_db else "—",
                "hint": "（補正済）" if status == "補正済" else "",
                "inArchive": bool(fmeta.get("inArchive")),
                "queueItem": queue_item,
                "faint": faint or None,
                "warpedPath": warped
                or ((result or {}).get("warpedPath") if result else "")
                or ((faint or {}).get("warpedPath") if faint else "")
                or "",
                "sourcePath": (result or {}).get("sourcePath")
                or fmeta.get("path")
                or "",
            }
        )

    stats = {
        "total": len(rows),
        "unprocessed": sum(1 for r in rows if r["status"] == "未処理"),
        "warped": sum(1 for r in rows if r["status"] == "補正済"),
        "processed": sum(1 for r in rows if r["status"] == "反映済"),
        "failed": sum(1 for r in rows if r["status"] == "失敗"),
        "faint": sum(1 for r in rows if r["status"] == "要確認（薄い）"),
        "inInbox": sum(1 for r in rows if not r.get("inArchive") and r["status"] != "反映済"),
    }
    return {"rows": rows, "stats": stats}
