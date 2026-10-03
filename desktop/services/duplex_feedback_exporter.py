"""表裏一体印刷（2テストを生徒IDで突き合わせた PDF 出力）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import fitz

from config import test_feedback
from models.output_repo import (
    FEEDBACK_IMAGE_BASIS_ORIGINAL,
    get_output_slots,
    normalize_feedback_image_basis,
)
from models.test_repo import get_all_results, list_tests, touch_progress
from services.feedback_exporter import build_row_pdf_document, sheet_source_ready
from services.feedback_pdf import save_feedback_pdf
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


def _missing_sheet_note(image_basis: str | None) -> str:
    if normalize_feedback_image_basis(image_basis) == FEEDBACK_IMAGE_BASIS_ORIGINAL:
        return "元画像または補正画像のある行がありません"
    return "補正画像のある行がありません"


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


def pair_duplex_students(
    front_rows: list[dict[str, Any]],
    back_rows: list[dict[str, Any]] | None,
) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """表の生徒を順に並べ、裏が無い ID は front_only として返す。"""
    front_map = rows_by_student_id(front_rows)
    back_map = rows_by_student_id(back_rows or [])
    if not front_map:
        raise DuplexMatchError(
            "有効な生徒ID（4桁ID・? なし）を持つ受験者が見つかりません。"
            "⑦で ID・氏名を割り当ててください。",
            front_count=0,
            back_count=len(back_map),
        )
    ordered = sorted(front_map.keys(), key=_sid_sort_key)
    front_only = [sid for sid in ordered if sid not in back_map]
    return ordered, front_map, back_map, front_only


def _ensure_output_slots(test_id: str, side_label: str) -> None:
    if not get_output_slots(test_id):
        raise ValueError(
            f"{side_label}側テスト: 合計欄が未設定です。先に出力欄を配置・保存してください。"
        )


def duplex_feedback_filename(student_id: str, student_name: str, *, front_only: bool = False) -> str:
    sid = _safe_name(student_id or "不明")
    sname = _safe_name(student_name or "")
    kind = "表" if front_only else "表裏"
    return f"個票_{kind}_{sid}_{sname}.pdf"


def _append_blank_page(doc: fitz.Document) -> None:
    """直前ページと同じ大きさの白紙を足し、次の表が裏面に回らないようにする。"""
    if doc.page_count <= 0:
        doc.new_page(width=595, height=842)
        return
    rect = doc[-1].rect
    doc.new_page(width=rect.width, height=rect.height)


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
    *,
    image_basis: str | None = None,
) -> None:
    doc = build_row_pdf_document(
        test_id, row, shared=shared, image_basis=image_basis
    )
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
    image_basis: str | None = None,
) -> bool:
    name = str(row.get("fileName") or student_id)
    if not sheet_source_ready(row, test_id=test_id, image_basis=image_basis):
        skipped.append(f"{side_label}:{name}")
        return False
    try:
        _append_row_pdf(master, test_id, row, shared, image_basis=image_basis)
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
    image_basis: str | None = None,
) -> dict[str, Any]:
    """表を出力し、裏がある生徒は続けて裏を付ける。裏が無い生徒は表だけ出す。

    全員を1つの PDF にするとき、裏テストを選んでいて裏が無い生徒には白紙を入れ、
    次の表が前の人の裏面に印刷されないようにする。裏側を空にすると全員が表のみ。
    """
    front_test_id = str(front_test_id or "").strip()
    back_test_id = str(back_test_id or "").strip()
    if not front_test_id:
        raise ValueError("表側のテストを選択してください。")
    if back_test_id and front_test_id == back_test_id:
        raise ValueError("表側と裏側は異なるテストを選択してください。")

    _ensure_output_slots(front_test_id, "表")
    if back_test_id:
        _ensure_output_slots(back_test_id, "裏")

    front_rows = _load_rows_with_extras(front_test_id)
    back_rows = _load_rows_with_extras(back_test_id) if back_test_id else []
    ordered_ids, front_map, back_map, _front_only = pair_duplex_students(
        front_rows, back_rows
    )
    insert_blank_back = bool(back_test_id)

    front_ctx = build_feedback_shared_context(front_test_id)
    back_ctx = (
        build_feedback_shared_context(back_test_id) if back_test_id else {}
    )
    out_dir = test_feedback(front_test_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(ordered_ids)
    saved_pages = 0
    saved_students = 0
    front_only_saved: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    combined_path: Path | None = None
    per_files: list[str] = []

    def _append_student(doc: fitz.Document, sid: str) -> tuple[int, bool]:
        """ページを足し、(追加ページ数, 裏なしで表だけ出せたか) を返す。"""
        f_row = front_map[sid]
        pages_before = doc.page_count
        front_ok = _try_append_side(
            doc,
            front_test_id,
            f_row,
            front_ctx,
            student_id=sid,
            side_label="表",
            errors=errors,
            skipped=skipped,
            image_basis=image_basis,
        )
        back_ok = False
        b_row = back_map.get(sid)
        if b_row is not None and back_test_id:
            back_ok = _try_append_side(
                doc,
                back_test_id,
                b_row,
                back_ctx,
                student_id=sid,
                side_label="裏",
                errors=errors,
                skipped=skipped,
                image_basis=image_basis,
            )
        only_front = front_ok and not back_ok
        if only_front and insert_blank_back:
            _append_blank_page(doc)
        added = doc.page_count - pages_before
        return added, only_front and added > 0

    if mode == "combined":
        master = fitz.open()
        try:
            for i, sid in enumerate(ordered_ids):
                if on_progress:
                    on_progress(i + 1, total, sid)
                added, only_front = _append_student(master, sid)
                saved_pages += added
                if added > 0:
                    saved_students += 1
                if only_front:
                    front_only_saved.append(sid)
            if saved_pages <= 0:
                raise ValueError(f"出力可能なページがありません（{_missing_sheet_note(image_basis)}）。")
            combined_name = (
                "個票_表面.pdf" if not back_test_id else DUPLEX_COMBINED_FILENAME
            )
            combined_path = out_dir / combined_name
            save_feedback_pdf(master, combined_path)
        finally:
            master.close()
    else:
        for i, sid in enumerate(ordered_ids):
            if on_progress:
                on_progress(i + 1, total, sid)
            f_row = front_map[sid]
            b_row = back_map.get(sid)
            name = str(f_row.get("name") or (b_row or {}).get("name") or "")
            mini = fitz.open()
            try:
                added, only_front = _append_student(mini, sid)
                if added <= 0:
                    continue
                out_path = out_dir / duplex_feedback_filename(
                    sid, name, front_only=only_front or not back_test_id
                )
                save_feedback_pdf(mini, out_path)
                per_files.append(str(out_path))
                saved_pages += added
                saved_students += 1
                if only_front:
                    front_only_saved.append(sid)
            finally:
                mini.close()

        if not per_files:
            raise ValueError(f"出力可能な PDF がありません（{_missing_sheet_note(image_basis)}）。")

    touch_progress(front_test_id, 10, "表裏一体個票出力済み")

    return {
        "mode": mode,
        "frontTestId": front_test_id,
        "backTestId": back_test_id,
        "studentCount": total,
        "savedStudents": saved_students,
        "frontOnly": front_only_saved,
        "pageCount": saved_pages,
        "outputDir": str(out_dir),
        "combinedFile": str(combined_path) if combined_path else None,
        "perStudentFiles": per_files,
        "skipped": skipped,
        "errors": errors,
    }
