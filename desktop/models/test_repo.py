"""テスト CRUD・テスト情報・記述欄・配点・採点結果。"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import (
    ensure_data_dirs,
    is_path_under_test_storage,
    require_path_under_test_storage,
    test_archive,
    test_dir,
    test_feedback,
    test_inbox,
    test_model,
    test_model_source,
    test_warped,
)
from constants import TEST_INFO_KEYS
from models.database import connect, get_active_test_id, init_db, set_active_test_id


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_test_dirs(test_id: str) -> str:
    ensure_data_dirs()
    for path in (
        test_inbox(test_id),
        test_warped(test_id),
        test_archive(test_id),
        test_model(test_id),
        test_model_source(test_id),
        test_feedback(test_id),
    ):
        path.mkdir(parents=True, exist_ok=True)
    return str(test_inbox(test_id))


def create_test(test_name: str, subject: str = "", datetime_str: str = "") -> dict[str, Any]:
    init_db()
    test_name = (test_name or "").strip()
    if not test_name:
        raise ValueError("テスト名は必須です。")

    test_id = str(uuid.uuid4())
    created_at = _now()
    inbox_path = _ensure_test_dirs(test_id)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tests(
                id, test_name, subject, datetime, created_at, status,
                current_step, student_folder_path, use_id_mark
            ) VALUES (?, ?, ?, ?, ?, '作成中', 0, ?, 1)
            """,
            (test_id, test_name, subject or "", datetime_str or "", created_at, inbox_path),
        )
        defaults = {
            "テスト名": test_name,
            "科目名": subject or "",
            "実施日時": datetime_str or "",
            "作成日時": created_at,
            "ステータス": "作成中",
            "現在ステップ": "1",
            "IDマーク欄使用": "true",
            "生徒回答フォルダID": inbox_path,
        }
        for key in TEST_INFO_KEYS:
            conn.execute(
                "INSERT OR IGNORE INTO test_info(test_id, key, value) VALUES (?, ?, ?)",
                (test_id, key, defaults.get(key, "")),
            )
        set_active_test_id(conn, test_id)
        conn.commit()

    return {
        "testSsId": test_id,
        "testName": test_name,
        "folderPath": inbox_path,
        "createdAt": created_at,
    }


def list_tests(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        active_id = get_active_test_id(conn)
        rows = conn.execute(
            """
            SELECT * FROM tests
            ORDER BY COALESCE(NULLIF(last_saved_at, ''), created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "testName": r["test_name"],
            "testSsId": r["id"],
            "createdAt": r["created_at"],
            "status": r["status"],
            "currentStep": str(r["current_step"]),
            "lastSavedAt": r["last_saved_at"] or "",
            "isActive": r["id"] == active_id,
            "folderPath": r["student_folder_path"],
        }
        for r in rows
    ]


def set_active_test(test_id: str) -> None:
    init_db()
    with connect() as conn:
        set_active_test_id(conn, test_id)
        conn.commit()


def clear_active_test() -> None:
    """アクティブテストの選択を解除する（テストデータ自体は削除しない）。"""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = 'active_test_id'")
        conn.commit()


def update_test(
    test_id: str,
    test_name: str,
    subject: str = "",
    datetime_str: str = "",
) -> None:
    """既存テストのテスト名・科目名・実施日時を更新する。"""
    init_db()
    test_name = (test_name or "").strip()
    if not test_name:
        raise ValueError("テスト名は必須です。")
    saved_at = _now()
    with connect() as conn:
        row = conn.execute("SELECT id FROM tests WHERE id = ?", (test_id,)).fetchone()
        if not row:
            raise ValueError("テストが見つかりません。")
        conn.execute(
            """
            UPDATE tests
            SET test_name = ?, subject = ?, datetime = ?, last_saved_at = ?
            WHERE id = ?
            """,
            (test_name, subject or "", datetime_str or "", saved_at, test_id),
        )
        for key, val in {
            "テスト名": test_name,
            "科目名": subject or "",
            "実施日時": datetime_str or "",
        }.items():
            conn.execute(
                """
                INSERT INTO test_info(test_id, key, value) VALUES (?, ?, ?)
                ON CONFLICT(test_id, key) DO UPDATE SET value = excluded.value
                """,
                (test_id, key, val),
            )
        conn.commit()


def get_test_info(test_id: str | None = None) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        tid = test_id or get_active_test_id(conn)
        if not tid:
            raise ValueError("アクティブなテストが選択されていません。")
        test = conn.execute("SELECT * FROM tests WHERE id = ?", (tid,)).fetchone()
        if not test:
            raise ValueError("テストが見つかりません。")
        info_rows = conn.execute(
            "SELECT key, value FROM test_info WHERE test_id = ?", (tid,)
        ).fetchall()
        info = {r["key"]: r["value"] for r in info_rows}
        legacy_inbox = info.get("生徒解答フォルダID", "").strip()
        if legacy_inbox and not info.get("生徒回答フォルダID", "").strip():
            info["生徒回答フォルダID"] = legacy_inbox
        fields = get_answer_fields_conn(conn, tid)
        points = get_points_conn(conn, tid)
    inbox_path = resolve_student_inbox(tid)
    return {
        "testSsId": tid,
        "testName": test["test_name"],
        "subject": test["subject"],
        "datetime": test["datetime"],
        "status": test["status"],
        "currentStep": test["current_step"],
        "folderPath": inbox_path,
        "modelAnswerPath": test["model_answer_path"],
        "refWidth": test["ref_width"],
        "refHeight": test["ref_height"],
        "info": info,
        "fields": fields,
        "points": points,
        "useIdMark": bool(test["use_id_mark"]) if "use_id_mark" in test.keys() else True,
    }


def get_use_id_mark(test_id: str) -> bool:
    """回答用紙に生徒IDマーク欄があり、③で OMR 読取するか。"""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT use_id_mark FROM tests WHERE id = ?", (test_id,)
        ).fetchone()
        if not row:
            return True
        return bool(row["use_id_mark"])


def set_use_id_mark(test_id: str, enabled: bool) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE tests SET use_id_mark = ? WHERE id = ?",
            (1 if enabled else 0, test_id),
        )
        _set_test_info(conn, test_id, "IDマーク欄使用", "true" if enabled else "false")
        conn.commit()


def _set_test_info(conn, test_id: str, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO test_info(test_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT(test_id, key) DO UPDATE SET value = excluded.value
        """,
        (test_id, key, str(value)),
    )


def touch_progress(test_id: str, step: int, status: str | None = None) -> None:
    with connect() as conn:
        now = _now()
        conn.execute(
            "UPDATE tests SET current_step = ?, last_saved_at = ?, status = COALESCE(?, status) WHERE id = ?",
            (step, now, status, test_id),
        )
        _set_test_info(conn, test_id, "現在ステップ", str(step))
        _set_test_info(conn, test_id, "最終保存日時", now)
        if status:
            _set_test_info(conn, test_id, "ステータス", status)
        conn.commit()


def save_student_folder(test_id: str, folder_path: str) -> None:
    folder_path = str(Path(folder_path).resolve())
    if not is_path_under_test_storage(test_id, folder_path):
        raise ValueError(
            f"生徒回答フォルダはテスト専用フォルダ内である必要があります: {test_dir(test_id)}"
        )
    with connect() as conn:
        conn.execute(
            "UPDATE tests SET student_folder_path = ? WHERE id = ?",
            (folder_path, test_id),
        )
        _set_test_info(conn, test_id, "生徒回答フォルダID", folder_path)
        conn.commit()


def resolve_student_inbox(test_id: str) -> str:
    """テスト専用 inbox を返す（他テストや外部フォルダ指定は inbox に正規化）。"""
    init_db()
    _ensure_test_dirs(test_id)
    canonical = str(test_inbox(test_id).resolve())
    with connect() as conn:
        row = conn.execute(
            "SELECT student_folder_path FROM tests WHERE id = ?", (test_id,)
        ).fetchone()
        stored = (row["student_folder_path"] or "").strip() if row else ""
    if (
        not stored
        or not is_path_under_test_storage(test_id, stored)
        or Path(stored).resolve() != Path(canonical)
    ):
        save_student_folder(test_id, canonical)
        return canonical
    return str(Path(stored).resolve())


def _unique_file_in_dir(dest_dir: Path, filename: str) -> Path:
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    ext = Path(filename).suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem}_{n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def copy_files_to_inbox(test_id: str, source_paths: list[str | Path]) -> list[str]:
    """複数ファイルをテスト専用 inbox にコピーする。"""
    _ensure_test_dirs(test_id)
    inbox = test_inbox(test_id)
    copied: list[str] = []
    for raw in source_paths:
        source = Path(raw)
        if not source.is_file():
            continue
        dest = _unique_file_in_dir(inbox, source.name)
        shutil.copy2(source, dest)
        copied.append(str(dest.resolve()))
    if not copied:
        raise ValueError("コピーできるファイルがありません。")
    save_student_folder(test_id, str(inbox.resolve()))
    return copied


def copy_student_sheet_to_inbox(test_id: str, source_path: str | Path) -> str:
    """生徒回答用紙を inbox（作業フォルダ）にコピーする。"""
    copied = copy_files_to_inbox(test_id, [source_path])
    return copied[0]


def save_model_answer_image(test_id: str, warped_bgr: Any) -> str:
    """補正済み模範解答画像を保存し、基準サイズを DB に記録する。"""
    from services.image_loader import imwrite_bgr

    _ensure_test_dirs(test_id)
    model_dir = test_model(test_id)
    fname = f"模範解答_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = model_dir / fname
    imwrite_bgr(path, warped_bgr, quality=90)
    h, w = warped_bgr.shape[:2]
    resolved = str(path.resolve())
    with connect() as conn:
        conn.execute(
            """
            UPDATE tests
            SET model_answer_path = ?, ref_width = ?, ref_height = ?
            WHERE id = ?
            """,
            (resolved, w, h, test_id),
        )
        _set_test_info(conn, test_id, "模範解答画像FileID", resolved)
        _set_test_info(conn, test_id, "基準画像幅", str(w))
        _set_test_info(conn, test_id, "基準画像高さ", str(h))
        conn.commit()
    return resolved


def archive_model_answer_source(test_id: str, source_path: str | Path) -> str:
    """模範解答として読み込んだ原稿ファイルを model/source に保存する。"""
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"原稿ファイルが見つかりません: {source}")
    _ensure_test_dirs(test_id)
    dest_dir = test_model_source(test_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower() or ".jpg"
    fname = f"模範解答_原稿_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    dest = dest_dir / fname
    shutil.copy2(source, dest)
    return str(dest.resolve())


def get_answer_fields_conn(conn, test_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT field_id, display_name, x, y, width, height, sort_order, ocr_lang
        FROM answer_fields WHERE test_id = ?
        ORDER BY sort_order, field_id
        """,
        (test_id,),
    ).fetchall()
    return [
        {
            "id": r["field_id"],
            "displayName": r["display_name"],
            "x": r["x"],
            "y": r["y"],
            "width": r["width"],
            "height": r["height"],
            "order": r["sort_order"],
            "ocrLang": _normalize_ocr_lang(r["ocr_lang"]),
        }
        for r in rows
    ]


def get_answer_fields(test_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        tid = test_id or get_active_test_id(conn)
        if not tid:
            return []
        return get_answer_fields_conn(conn, tid)


def _normalize_ocr_lang(value: str | None) -> str:
    return "ja" if str(value or "").lower() == "ja" else "en"


def save_answer_fields(test_id: str, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fields:
        raise ValueError("記述欄が空のため保存しません。")
    normalized = []
    for i, f in enumerate(fields):
        fid = str(f.get("id") or f.get("field_id") or "").strip()
        if not fid:
            raise ValueError("記述欄IDが空です。")
        normalized.append(
            {
                "id": fid,
                "displayName": str(f.get("displayName") or fid),
                "x": int(f.get("x") or 0),
                "y": int(f.get("y") or 0),
                "width": int(f.get("width") or 0),
                "height": int(f.get("height") or 0),
                "order": int(f.get("order") or i + 1),
                "ocrLang": _normalize_ocr_lang(f.get("ocrLang")),
            }
        )
    with connect() as conn:
        conn.execute("DELETE FROM answer_fields WHERE test_id = ?", (test_id,))
        for f in normalized:
            conn.execute(
                """
                INSERT INTO answer_fields(
                    test_id, field_id, display_name, x, y, width, height, sort_order, ocr_lang
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    f["id"],
                    f["displayName"],
                    f["x"],
                    f["y"],
                    f["width"],
                    f["height"],
                    f["order"],
                    f["ocrLang"],
                ),
            )
        touch_progress_conn(conn, test_id, 1)
        conn.commit()
    return normalized


def touch_progress_conn(conn, test_id: str, step: int, status: str | None = None) -> None:
    now = _now()
    conn.execute(
        "UPDATE tests SET current_step = ?, last_saved_at = ?, status = COALESCE(?, status) WHERE id = ?",
        (step, now, status, test_id),
    )
    _set_test_info(conn, test_id, "現在ステップ", str(step))
    _set_test_info(conn, test_id, "最終保存日時", now)


def get_points_conn(conn, test_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT field_id, points FROM points WHERE test_id = ?", (test_id,)
    ).fetchall()
    return {r["field_id"]: int(r["points"]) for r in rows}


def save_points(test_id: str, points_map: dict[str, int]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM points WHERE test_id = ?", (test_id,))
        for field_id, pts in points_map.items():
            conn.execute(
                "INSERT INTO points(test_id, field_id, points) VALUES (?, ?, ?)",
                (test_id, field_id, int(pts)),
            )
        touch_progress_conn(conn, test_id, 2)
        conn.commit()


def normalize_file_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).lower()


def get_processed_file_names(test_id: str) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT file_name FROM results WHERE test_id = ?", (test_id,)
        ).fetchall()
    return {normalize_file_name(r["file_name"]) for r in rows}


def append_result_row(
    test_id: str,
    file_name: str,
    source_path: str,
    warped_path: str,
    student_id: str,
    text_mapping: dict[str, str],
) -> bool:
    """Returns True if written, False if skipped (duplicate)."""
    norm = normalize_file_name(file_name)
    if norm in get_processed_file_names(test_id):
        return False
    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO results(
                    test_id, student_id, file_name, source_path, warped_path,
                    texts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    student_id or "",
                    file_name,
                    source_path or "",
                    warped_path or "",
                    json.dumps(text_mapping, ensure_ascii=False),
                    _now(),
                ),
            )
            touch_progress_conn(conn, test_id, 3, "テキスト化中")
            conn.commit()
            return True
        except Exception:
            return False


def flush_result_rows(test_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    written = 0
    updated = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    written_names: list[str] = []
    existing = get_processed_file_names(test_id)
    for r in rows:
        file_name = str(r.get("fileName") or r.get("file_name") or "")
        try:
            norm = normalize_file_name(file_name)
            text_mapping = r.get("textMapping") or r.get("text_mapping") or {}
            source_path = str(r.get("sourcePath") or r.get("source_path") or "")
            warped_path = str(r.get("warpedPath") or r.get("warped_path") or "")
            student_id = r.get("studentId") or r.get("student_id") or ""
            if norm in existing:
                upsert_result_texts(
                    test_id,
                    file_name,
                    text_mapping,
                    source_path=source_path,
                    warped_path=warped_path,
                    student_id=student_id if student_id else None,
                )
                updated += 1
                written_names.append(file_name)
            else:
                ok = append_result_row(
                    test_id,
                    file_name,
                    source_path,
                    warped_path,
                    student_id,
                    text_mapping,
                )
                if ok:
                    written += 1
                    written_names.append(file_name)
                    existing.add(norm)
                else:
                    skipped += 1
        except Exception as e:
            errors.append({"fileName": file_name, "error": str(e)})
    return {
        "written": written,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "writtenFileNames": written_names,
    }


def bootstrap_empty_result_row(
    test_id: str,
    file_name: str,
    *,
    source_path: str,
    warped_path: str,
    field_ids: list[str],
) -> str:
    """手動採点用の空テキスト行を INSERT、既存行はパスのみ UPDATE（判定・得点・テキストは保持）。

    戻り値: inserted / updated / skipped
    """
    file_name = str(file_name or "").strip()
    if not file_name:
        raise ValueError("ファイル名が空です。")
    if not str(warped_path or "").strip():
        return "skipped"

    empty_texts = {str(fid): "" for fid in field_ids if fid}
    now = _now()

    with connect() as conn:
        existing = conn.execute(
            "SELECT id, texts_json, judgments_json, scores_json FROM results "
            "WHERE test_id = ? AND file_name = ?",
            (test_id, file_name),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE results SET source_path = ?, warped_path = ?
                WHERE id = ?
                """,
                (source_path or "", warped_path or "", existing["id"]),
            )
            touch_progress_conn(conn, test_id, 4, "手動採点準備")
            conn.commit()
            return "updated"

        conn.execute(
            """
            INSERT INTO results(
                test_id, student_id, file_name, source_path, warped_path, name,
                texts_json, judgments_json, scores_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_id,
                "",
                file_name,
                source_path or "",
                warped_path or "",
                "",
                json.dumps(empty_texts, ensure_ascii=False),
                "{}",
                "{}",
                now,
            ),
        )
        touch_progress_conn(conn, test_id, 4, "手動採点準備")
        conn.commit()
        return "inserted"


def get_result_preview(test_id: str) -> list[dict[str, Any]]:
    return get_all_results(test_id)


def get_all_results(test_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM results WHERE test_id = ? ORDER BY file_name",
            (test_id,),
        ).fetchall()
    out = []
    for r in rows:
        texts = json.loads(r["texts_json"] or "{}")
        judgments = json.loads(r["judgments_json"] or "{}") if "judgments_json" in r.keys() else {}
        scores = json.loads(r["scores_json"] or "{}") if "scores_json" in r.keys() else {}
        out.append(
            {
                "id": r["id"],
                "fileName": r["file_name"],
                "studentId": r["student_id"],
                "name": r["name"] if "name" in r.keys() else "",
                "textMapping": texts,
                "judgments": judgments,
                "scores": scores,
                "warpedPath": r["warped_path"],
                "sourcePath": r["source_path"],
            }
        )
    return out


def update_results_field_grades(
    test_id: str,
    field_id: str,
    result_ids: list[int],
    judgment: str,
    score: int,
) -> int:
    """手動採点: 指定結果行の記述欄判定・得点を一括更新。"""
    if not result_ids:
        return 0
    fid = str(field_id or "").strip()
    if not fid:
        raise ValueError("記述欄IDが空です。")
    from models.grading_status import normalize_judgment

    j = normalize_judgment(judgment) or str(judgment or "").strip()
    sc = int(score)
    updated = 0
    with connect() as conn:
        for rid in result_ids:
            row = conn.execute(
                "SELECT id, judgments_json, scores_json FROM results WHERE test_id = ? AND id = ?",
                (test_id, int(rid)),
            ).fetchone()
            if not row:
                continue
            judgments = json.loads(row["judgments_json"] or "{}")
            scores = json.loads(row["scores_json"] or "{}")
            judgments[fid] = j
            scores[fid] = sc
            conn.execute(
                "UPDATE results SET judgments_json = ?, scores_json = ? WHERE id = ?",
                (
                    json.dumps(judgments, ensure_ascii=False),
                    json.dumps(scores, ensure_ascii=False),
                    row["id"],
                ),
            )
            updated += 1
        if updated:
            touch_progress_conn(conn, test_id, 5, "手動採点")
        conn.commit()
    return updated


def rewrite_field_texts(
    test_id: str,
    field_id: str,
    should_rewrite: Callable[[str], bool],
    new_text: str | Callable[[str], str],
    *,
    transform_mode: bool = False,
) -> int:
    """
    採点結果の texts_json 内、指定 field_id のテキストを書き換える。
    transform_mode=True のとき new_text は (old) -> new の関数。
    """
    canonical = ""
    if not transform_mode:
        canonical = str(new_text or "").strip() or "なし"

    updated = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, texts_json FROM results WHERE test_id = ?",
            (test_id,),
        ).fetchall()
        for row in rows:
            texts = json.loads(row["texts_json"] or "{}")
            old_val = str(texts.get(field_id, "") or "").strip() or "なし"
            if not should_rewrite(old_val):
                continue
            if transform_mode:
                assert callable(new_text)
                new_val = str(new_text(old_val)).strip() or "なし"
            else:
                new_val = canonical
            if old_val == new_val:
                continue
            texts[field_id] = new_val
            conn.execute(
                "UPDATE results SET texts_json = ? WHERE id = ?",
                (json.dumps(texts, ensure_ascii=False), row["id"]),
            )
            updated += 1
        conn.commit()
    return updated


def upsert_result_texts(
    test_id: str,
    file_name: str,
    text_mapping: dict[str, str],
    *,
    source_path: str = "",
    warped_path: str = "",
    student_id: str | None = None,
) -> str:
    """texts（とパス類）だけ更新し、既存の judgments / scores は保持する。

    行が無い場合は INSERT（判定・得点は空）。戻り値: inserted / updated。
    """
    file_name = str(file_name or "").strip()
    if not file_name:
        raise ValueError("ファイル名が空です。")
    texts_json = json.dumps(text_mapping or {}, ensure_ascii=False)
    now = _now()

    with connect() as conn:
        existing = conn.execute(
            "SELECT id, student_id, source_path, warped_path, name, "
            "judgments_json, scores_json FROM results "
            "WHERE test_id = ? AND file_name = ?",
            (test_id, file_name),
        ).fetchone()
        if existing:
            sid = (
                student_id
                if student_id is not None
                else (existing["student_id"] or "")
            )
            src = source_path or (existing["source_path"] or "")
            warp = warped_path or (existing["warped_path"] or "")
            conn.execute(
                """
                UPDATE results SET
                    student_id = ?, source_path = ?, warped_path = ?,
                    texts_json = ?
                WHERE id = ?
                """,
                (sid or "", src, warp, texts_json, existing["id"]),
            )
            touch_progress_conn(conn, test_id, 3, "テキスト化中")
            conn.commit()
            return "updated"
        conn.execute(
            """
            INSERT INTO results(
                test_id, student_id, file_name, source_path, warped_path, name,
                texts_json, judgments_json, scores_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_id,
                student_id or "",
                file_name,
                source_path or "",
                warped_path or "",
                "",
                texts_json,
                "{}",
                "{}",
                now,
            ),
        )
        touch_progress_conn(conn, test_id, 3, "テキスト化中")
        conn.commit()
        return "inserted"


def get_result_by_file_name(test_id: str, file_name: str) -> dict[str, Any] | None:
    """normalize 後のファイル名で 1 件取得。無ければ None。"""
    key = normalize_file_name(file_name)
    if not key:
        return None
    for row in get_all_results(test_id):
        if normalize_file_name(row.get("fileName") or "") == key:
            return row
    return None


def upsert_result_row(
    test_id: str,
    file_name: str,
    *,
    source_path: str = "",
    warped_path: str = "",
    student_id: str = "",
    name: str = "",
    text_mapping: dict[str, str] | None = None,
    judgments: dict[str, str] | None = None,
    scores: dict[str, Any] | None = None,
) -> str:
    """採点結果を INSERT または UPDATE。戻り値: inserted / updated。"""
    file_name = str(file_name or "").strip()
    if not file_name:
        raise ValueError("ファイル名が空です。")
    texts_json = json.dumps(text_mapping or {}, ensure_ascii=False)
    judgments_json = json.dumps(judgments or {}, ensure_ascii=False)
    scores_json = json.dumps(scores or {}, ensure_ascii=False)
    now = _now()

    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM results WHERE test_id = ? AND file_name = ?",
            (test_id, file_name),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE results SET
                    student_id = ?, source_path = ?, warped_path = ?, name = ?,
                    texts_json = ?, judgments_json = ?, scores_json = ?
                WHERE id = ?
                """,
                (
                    student_id or "",
                    source_path or "",
                    warped_path or "",
                    name or "",
                    texts_json,
                    judgments_json,
                    scores_json,
                    existing["id"],
                ),
            )
            touch_progress_conn(conn, test_id, 3, "テキスト化中")
            conn.commit()
            return "updated"
        conn.execute(
            """
            INSERT INTO results(
                test_id, student_id, file_name, source_path, warped_path, name,
                texts_json, judgments_json, scores_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_id,
                student_id or "",
                file_name,
                source_path or "",
                warped_path or "",
                name or "",
                texts_json,
                judgments_json,
                scores_json,
                now,
            ),
        )
        touch_progress_conn(conn, test_id, 3, "テキスト化中")
        conn.commit()
        return "inserted"


def _excel_cell_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


def import_results_from_excel(test_id: str, path: str) -> dict[str, Any]:
    """エクスポート済み Excel から採点結果を取り込み、③ 一覧の再現用に DB を更新する。"""
    import pandas as pd

    df = pd.read_excel(path, sheet_name=0)
    if df.empty:
        raise ValueError("Excel にデータ行がありません。")
    if "ファイル名" not in df.columns:
        raise ValueError("「ファイル名」列が見つかりません。③ でエクスポートした Excel を選んでください。")

    fields = get_answer_fields(test_id)
    inserted = 0
    updated = 0
    skipped = 0

    for _, row in df.iterrows():
        file_name = _excel_cell_str(row.get("ファイル名"))
        if not file_name:
            skipped += 1
            continue

        texts: dict[str, str] = {}
        judgments: dict[str, str] = {}
        scores: dict[str, Any] = {}
        for f in fields:
            label = f["displayName"] or f["id"]
            fid = f["id"]
            tcol, jcol, scol = f"{label}_テキスト", f"{label}_判定", f"{label}_得点"
            if tcol in df.columns:
                t = _excel_cell_str(row.get(tcol))
                texts[fid] = t or "なし"
            if jcol in df.columns:
                judgments[fid] = _excel_cell_str(row.get(jcol))
            if scol in df.columns:
                raw = _excel_cell_str(row.get(scol))
                if raw:
                    try:
                        scores[fid] = int(float(raw))
                    except ValueError:
                        scores[fid] = raw

        action = upsert_result_row(
            test_id,
            file_name,
            source_path=_excel_cell_str(row.get("ファイルID")),
            warped_path=_excel_cell_str(row.get("補正画像FileID")),
            student_id=_excel_cell_str(row.get("生徒ID")),
            name=_excel_cell_str(row.get("氏名")),
            text_mapping=texts,
            judgments=judgments,
            scores=scores,
        )
        if action == "inserted":
            inserted += 1
        else:
            updated += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total": inserted + updated,
    }


def export_results_to_excel(test_id: str, output_path: str) -> str:
    """採点結果を GAS 互換のワイド形式 Excel にエクスポート。"""
    import pandas as pd

    path = str(require_path_under_test_storage(test_id, output_path, label="Excel の保存先"))
    headers, rows = build_result_export_rows(test_id)
    df = pd.DataFrame(rows, columns=headers)
    df.to_excel(path, index=False, sheet_name="採点結果")
    return path


def escape_tsv_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    if any(c in s for c in ("\t", "\n", "\r", '"')):
        return '"' + s.replace('"', '""') + '"'
    return s


def build_result_export_rows(test_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    """採点結果のエクスポート用ヘッダーと行データ。"""
    fields = get_answer_fields(test_id)
    preview = get_all_results(test_id)
    headers = ["生徒ID", "ファイル名", "ファイルID", "補正画像FileID", "氏名"]
    for f in fields:
        label = f["displayName"] or f["id"]
        headers.extend([f"{label}_テキスト", f"{label}_判定", f"{label}_得点"])

    rows: list[dict[str, Any]] = []
    for item in preview:
        row = {
            "生徒ID": item.get("studentId") or "",
            "ファイル名": item.get("fileName") or "",
            "ファイルID": item.get("sourcePath") or "",
            "補正画像FileID": item.get("warpedPath") or "",
            "氏名": item.get("name") or "",
        }
        for f in fields:
            label = f["displayName"] or f["id"]
            fid = f["id"]
            row[f"{label}_テキスト"] = (item.get("textMapping") or {}).get(fid, "なし")
            row[f"{label}_判定"] = (item.get("judgments") or {}).get(fid, "")
            row[f"{label}_得点"] = (item.get("scores") or {}).get(fid, "")
        rows.append(row)
    return headers, rows


def build_results_tsv(test_id: str) -> str:
    """採点結果を TSV 文字列にする（スプレッドシート貼付用）。"""
    headers, rows = build_result_export_rows(test_id)
    if not rows:
        return ""
    lines = ["\t".join(escape_tsv_cell(h) for h in headers)]
    for row in rows:
        lines.append("\t".join(escape_tsv_cell(row.get(h, "")) for h in headers))
    return "\n".join(lines)


def build_pending_rows_tsv(test_id: str, pending_rows: list[dict[str, Any]]) -> str:
    """バッチ直後の未反映行だけ TSV にする（GAS の手動貼付用）。"""
    if not pending_rows:
        return ""
    fields = get_answer_fields(test_id)
    headers = ["生徒ID", "ファイル名", "ファイルID", "補正画像FileID", "氏名"]
    for f in fields:
        label = f["displayName"] or f["id"]
        headers.extend([f"{label}_テキスト", f"{label}_判定", f"{label}_得点"])

    lines = ["\t".join(escape_tsv_cell(h) for h in headers)]
    for item in pending_rows:
        row = {
            "生徒ID": item.get("studentId") or "",
            "ファイル名": item.get("fileName") or "",
            "ファイルID": item.get("sourcePath") or "",
            "補正画像FileID": item.get("warpedPath") or "",
            "氏名": "",
        }
        texts = item.get("textMapping") or {}
        for f in fields:
            label = f["displayName"] or f["id"]
            fid = f["id"]
            row[f"{label}_テキスト"] = texts.get(fid, "なし")
            row[f"{label}_判定"] = ""
            row[f"{label}_得点"] = ""
        lines.append("\t".join(escape_tsv_cell(row.get(h, "")) for h in headers))
    return "\n".join(lines)


# --- ③ テキスト化: 失敗記録・リセット ---

def _step3_failed_key(test_id: str) -> str:
    return f"step3_failed_{test_id}"


def get_step3_failed(test_id: str) -> dict[str, dict[str, str]]:
    """normalize_file_name → {fileName, error, stage}"""
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (_step3_failed_key(test_id),)
        ).fetchone()
    if not row or not row["value"]:
        return {}
    try:
        raw = json.loads(row["value"])
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for item in raw if isinstance(raw, list) else []:
        name = str(item.get("fileName") or "")
        key = normalize_file_name(name)
        if key:
            out[key] = {
                "fileName": name,
                "error": str(item.get("error") or ""),
                "stage": str(item.get("stage") or ""),
            }
    return out


def save_step3_failed(test_id: str, failed: dict[str, dict[str, str]]) -> None:
    rows = list(failed.values())
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_step3_failed_key(test_id), json.dumps(rows, ensure_ascii=False)),
        )
        conn.commit()


def set_step3_failed_entry(
    test_id: str, file_name: str, error: str, stage: str = ""
) -> None:
    failed = get_step3_failed(test_id)
    key = normalize_file_name(file_name)
    failed[key] = {"fileName": file_name, "error": error, "stage": stage}
    save_step3_failed(test_id, failed)


def clear_step3_failed_entry(test_id: str, file_name: str) -> None:
    failed = get_step3_failed(test_id)
    failed.pop(normalize_file_name(file_name), None)
    save_step3_failed(test_id, failed)


def clear_step3_failed(test_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", (_step3_failed_key(test_id),))
        conn.commit()


# --- ③ テキスト化: 薄い字の要確認記録 ---

def _step3_faint_key(test_id: str) -> str:
    return f"step3_faint_{test_id}"


def get_step3_faint(test_id: str) -> dict[str, dict[str, Any]]:
    """normalize_file_name → {fileName, reason, fieldId, metrics, failedCriteria}"""
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (_step3_faint_key(test_id),)
        ).fetchone()
    if not row or not row["value"]:
        return {}
    try:
        raw = json.loads(row["value"])
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        name = str(item.get("fileName") or "")
        key = normalize_file_name(name)
        if key:
            out[key] = {
                "fileName": name,
                "reason": str(item.get("reason") or ""),
                "fieldId": str(item.get("fieldId") or ""),
                "metrics": dict(item.get("metrics") or {}),
                "failedCriteria": list(item.get("failedCriteria") or []),
                "warpedPath": str(item.get("warpedPath") or ""),
            }
    return out


def save_step3_faint(test_id: str, faint: dict[str, dict[str, Any]]) -> None:
    rows = list(faint.values())
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_step3_faint_key(test_id), json.dumps(rows, ensure_ascii=False)),
        )
        conn.commit()


def set_step3_faint_entry(test_id: str, entry: dict[str, Any]) -> None:
    faint = get_step3_faint(test_id)
    name = str(entry.get("fileName") or "")
    key = normalize_file_name(name)
    if not key:
        return
    faint[key] = {
        "fileName": name,
        "reason": str(entry.get("reason") or ""),
        "fieldId": str(entry.get("fieldId") or ""),
        "metrics": dict(entry.get("metrics") or {}),
        "failedCriteria": list(entry.get("failedCriteria") or []),
        "warpedPath": str(entry.get("warpedPath") or ""),
    }
    save_step3_faint(test_id, faint)


def clear_step3_faint_entry(test_id: str, file_name: str) -> None:
    faint = get_step3_faint(test_id)
    faint.pop(normalize_file_name(file_name), None)
    save_step3_faint(test_id, faint)


def clear_step3_faint(test_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", (_step3_faint_key(test_id),))
        conn.commit()


def reset_step3_data(test_id: str) -> dict[str, int]:
    """⑤〜⑦ の処理結果をすべて初期化（後方互換）。"""
    return reset_step5_trim_data(test_id)


def reset_step5_trim_data(test_id: str) -> dict[str, int]:
    """⑤トリミング以降を初期化（OCR・補正・薄字・失敗記録、原本復元）。"""
    import shutil
    from pathlib import Path

    info = get_test_info(test_id)
    folder = Path(info.get("folderPath") or test_inbox(test_id))
    folder.mkdir(parents=True, exist_ok=True)
    archive = test_archive(test_id)
    warped = test_warped(test_id)

    restored = 0
    if archive.exists():
        for p in archive.iterdir():
            if not p.is_file():
                continue
            dest = folder / p.name
            if not dest.exists():
                shutil.move(str(p), str(dest))
                restored += 1

    deleted_warped = 0
    if warped.exists():
        for p in warped.iterdir():
            if p.is_file():
                p.unlink()
                deleted_warped += 1

    with connect() as conn:
        cur = conn.execute("DELETE FROM results WHERE test_id = ?", (test_id,))
        deleted_results = cur.rowcount
        conn.execute("DELETE FROM app_state WHERE key = ?", (_step3_failed_key(test_id),))
        conn.execute("DELETE FROM app_state WHERE key = ?", (_step3_faint_key(test_id),))
        touch_progress_conn(conn, test_id, 2, "リセット済み")
        conn.commit()

    return {
        "restored": restored,
        "deletedWarped": deleted_warped,
        "deletedResults": deleted_results,
    }


def reset_step6_faint_data(test_id: str) -> dict[str, int]:
    """⑥薄字補正を初期化（薄字記録・強調上書きの巻き戻し）。"""
    import shutil
    from pathlib import Path

    warped = test_warped(test_id)
    restored_enhanced = 0
    if warped.exists():
        for backup in warped.glob("補正_*_原.jpg"):
            if not backup.is_file():
                continue
            original_name = backup.stem.replace("_原", "", 1) + backup.suffix
            dest = backup.parent / original_name
            shutil.copy2(str(backup), str(dest))
            backup.unlink()
            restored_enhanced += 1

    failed = get_step3_failed(test_id)
    faint_removed = 0
    for key, entry in list(failed.items()):
        if str(entry.get("stage") or "") == "faint":
            failed.pop(key, None)
            faint_removed += 1
    save_step3_failed(test_id, failed)
    clear_step3_faint(test_id)

    return {"restoredEnhanced": restored_enhanced, "faintRemoved": faint_removed}


def reset_step7_ocr_data(test_id: str) -> dict[str, int]:
    """⑦OCR結果のみ削除（補正画像・薄字記録は保持）。"""
    with connect() as conn:
        cur = conn.execute("DELETE FROM results WHERE test_id = ?", (test_id,))
        deleted_results = cur.rowcount
        touch_progress_conn(conn, test_id, 2, "OCRリセット済み")
        conn.commit()
    return {"deletedResults": deleted_results}
