"""表裏一体印刷（2テストを生徒IDで突き合わせた PDF 出力）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import fitz

from config import test_feedback
from models.output_repo import get_output_slots
from models.test_repo import get_all_results, list_tests, touch_progress
from services.feedback_exporter import build_row_pdf_document
from services.feedback_renderer import (
    _load_rows_with_extras,
    build_feedback_shared_context,
)

DUPLEX_COMBINED_FILENAME = "個票_表裏一体.pdf"
DuplexExportMode = Literal["combined", "per_student"]


class DuplexMatchError(ValueError):
    """表裏テストの受験者ID集合が一致しない。"""

    def __init__(
        self,
        message: str,
        *,
        front_count: int = 0,
        back_count: int = 0,
        front_only: list[str] | None = None,
        back_only: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.front_count = front_count
        self.back_count = back_count
        self.front_only = list(front_only or [])
        self.back_only = list(back_only or [])


def _safe_name(value: str) -> str:
    return "".join(c for c in str(value or "") if c not in '\\/:*?"<>|').strip() or "無名"


def _sid_sort_key(sid: str) -> tuple:
    s = str(sid or "").strip()
    try:
        return (0, float(s), s)
    except ValueError:
        return (1, 0.0, s.lower())


def is_valid_student_id(student_id: str) -> bool:
    sid = str(student_id or "").strip()
    return bool(sid) and "?" not in sid


def rows_by_student_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """有効な studentId の行のみ。同一IDは先勝ち。"""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("studentId") or "").strip()
        if not is_valid_student_id(sid):
            continue
        if sid not in out:
            out[sid] = row
    return out


def validate_duplex_student_sets(
    front_rows: list[dict[str, Any]],
    back_rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    front_map = rows_by_student_id(front_rows)
    back_map = rows_by_student_id(back_rows)
    front_ids = set(front_map.keys())
    back_ids = set(back_map.keys())
    if front_ids != back_ids:
        only_front = sorted(front_ids - back_ids, key=_sid_sort_key)
        only_back = sorted(back_ids - front_ids, key=_sid_sort_key)
        parts = [
            f"受験者IDが一致しません: 表 {len(front_ids)} 名 / 裏 {len(back_ids)} 名。"
        ]
        if only_front:
            sample = ", ".join(only_front[:8])
            more = f" 他{len(only_front) - 8}件" if len(only_front) > 8 else ""
            parts.append(f"表のみ: {sample}{more}")
        if only_back:
            sample = ", ".join(only_back[:8])
            more = f" 他{len(only_back) - 8}件" if len(only_back) > 8 else ""
            parts.append(f"裏のみ: {sample}{more}")
        raise DuplexMatchError(
            "\n".join(parts),
            front_count=len(front_ids),
            back_count=len(back_ids),
            front_only=only_front,
            back_only=only_back,
        )
    if not front_ids:
        raise DuplexMatchError(
            "有効な生徒ID（4桁ID・? なし）を持つ受験者が見つかりません。"
            "⑦で ID・氏名を割り当ててください。",
        )
    ordered = sorted(front_ids, key=_sid_sort_key)
    return ordered, front_map, back_map


def _ensure_output_slots(test_id: str, side_label: str) -> None:
    if not get_output_slots(test_id):
        raise ValueError(
            f"{side_label}側テスト: 合計欄が未設定です。先に出力欄を配置・保存してください。"
        )


def duplex_feedback_filename(student_id: str, student_name: str) -> str:
    sid = _safe_name(student_id or "不明")
    sname = _safe_name(student_name or "")
    return f"個票_表裏_{sid}_{sname}.pdf"


def list_duplex_candidate_tests(limit: int = 50) -> list[dict[str, Any]]:
    """表裏一体印刷の候補テスト（結果あり・ID割当が半数超）。"""
    candidates: list[dict[str, Any]] = []
    for t in list_tests(limit):
        tid = str(t.get("testSsId") or "")
        if not tid:
            continue
        rows = get_all_results(tid)
        if not rows:
            continue
        valid = sum(1 for r in rows if is_valid_student_id(str(r.get("studentId") or "")))
        if valid > 0 and valid > len(rows) / 2:
            item = dict(t)
            item["resultCount"] = len(rows)
            item["validIdCount"] = valid
            candidates.append(item)
    return candidates


def _append_row_pdf(
    master: fitz.Document,
    test_id: str,
    row: dict[str, Any],
    shared: dict[str, Any],
) -> None:
    doc = build_row_pdf_document(test_id, row, shared=shared)
    try:
        master.insert_pdf(doc)
    finally:
        doc.close()


def _try_append_side(
    master: fitz.Document,
    test_id: str,
    row: dict[str, Any],
    shared: dict[str, Any],
    *,
    student_id: str,
    side_label: str,
    errors: list[dict[str, str]],
    skipped: list[str],
) -> bool:
    warped = str(row.get("warpedPath") or "").strip()
    name = str(row.get("fileName") or student_id)
    if not warped or not Path(warped).exists():
        skipped.append(f"{side_label}:{name}")
        return False
    try:
        _append_row_pdf(master, test_id, row, shared)
        return True
    except Exception as exc:
        errors.append(
            {
                "studentId": student_id,
                "side": side_label,
                "fileName": name,
                "error": str(exc),
            }
        )
        return False


def batch_export_duplex_feedback(
    front_test_id: str,
    back_test_id: str,
    *,
    mode: DuplexExportMode = "combined",
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """表裏2テストを生徒IDで突き合わせ、奇数＝表・偶数＝裏の PDF を出力する。"""
    front_test_id = str(front_test_id or "").strip()
    back_test_id = str(back_test_id or "").strip()
    if not front_test_id or not back_test_id:
        raise ValueError("表側・裏側の両方のテストを選択してください。")
    if front_test_id == back_test_id:
        raise ValueError("表側と裏側は異なるテストを選択してください。")

    _ensure_output_slots(front_test_id, "表")
    _ensure_output_slots(back_test_id, "裏")

    front_rows = _load_rows_with_extras(front_test_id)
    back_rows = _load_rows_with_extras(back_test_id)
    ordered_ids, front_map, back_map = validate_duplex_student_sets(
        front_rows, back_rows
    )

    front_ctx = build_feedback_shared_context(front_test_id)
    back_ctx = build_feedback_shared_context(back_test_id)
    out_dir = test_feedback(front_test_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(ordered_ids)
    saved_pages = 0
    saved_students = 0
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    combined_path: Path | None = None
    per_files: list[str] = []

    if mode == "combined":
        master = fitz.open()
        try:
            for i, sid in enumerate(ordered_ids):
                if on_progress:
                    on_progress(i + 1, total, sid)
                f_row = front_map[sid]
                b_row = back_map[sid]
                pages_before = master.page_count
                _try_append_side(
                    master,
                    front_test_id,
                    f_row,
                    front_ctx,
                    student_id=sid,
                    side_label="表",
                    errors=errors,
                    skipped=skipped,
                )
                _try_append_side(
                    master,
                    back_test_id,
                    b_row,
                    back_ctx,
                    student_id=sid,
                    side_label="裏",
                    errors=errors,
                    skipped=skipped,
                )
                added = master.page_count - pages_before
                saved_pages += added
                if added >= 2:
                    saved_students += 1
            if saved_pages <= 0:
                raise ValueError(
                    "出力可能な表裏ページがありません（補正画像のある行がありません）。"
                )
            combined_path = out_dir / DUPLEX_COMBINED_FILENAME
            master.save(str(combined_path))
        finally:
            master.close()
    else:
        for i, sid in enumerate(ordered_ids):
            if on_progress:
                on_progress(i + 1, total, sid)
            f_row = front_map[sid]
            b_row = back_map[sid]
            name = str(f_row.get("name") or b_row.get("name") or "")
            mini = fitz.open()
            try:
                pages_before = mini.page_count
                _try_append_side(
                    mini,
                    front_test_id,
                    f_row,
                    front_ctx,
                    student_id=sid,
                    side_label="表",
                    errors=errors,
                    skipped=skipped,
                )
                _try_append_side(
                    mini,
                    back_test_id,
                    b_row,
                    back_ctx,
                    student_id=sid,
                    side_label="裏",
                    errors=errors,
                    skipped=skipped,
                )
                added = mini.page_count - pages_before
                if added <= 0:
                    continue
                out_path = out_dir / duplex_feedback_filename(sid, name)
                mini.save(str(out_path))
                per_files.append(str(out_path))
                saved_pages += added
                if added >= 2:
                    saved_students += 1
            finally:
                mini.close()

        if not per_files:
            raise ValueError(
                "出力可能な表裏 PDF がありません（補正画像のある行がありません）。"
            )

    touch_progress(front_test_id, 10, "表裏一体個票出力済み")

    return {
        "mode": mode,
        "frontTestId": front_test_id,
        "backTestId": back_test_id,
        "studentCount": total,
        "savedStudents": saved_students,
        "pageCount": saved_pages,
        "outputDir": str(out_dir),
        "combinedFile": str(combined_path) if combined_path else None,
        "perStudentFiles": per_files,
        "skipped": skipped,
        "errors": errors,
    }
