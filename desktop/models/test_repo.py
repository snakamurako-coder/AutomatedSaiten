"""テスト CRUD・テスト情報・記述欄・配点・採点結果。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Callable

from config import (
    ensure_data_dirs,
    test_archive,
    test_feedback,
    test_inbox,
    test_model,
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
            "現在ステップ": "0",
            "IDマーク欄使用": "true",
            "生徒解答フォルダID": inbox_path,
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
        fields = get_answer_fields_conn(conn, tid)
        points = get_points_conn(conn, tid)
    return {
        "testSsId": tid,
        "testName": test["test_name"],
        "subject": test["subject"],
        "datetime": test["datetime"],
        "status": test["status"],
        "currentStep": test["current_step"],
        "folderPath": test["student_folder_path"],
        "modelAnswerPath": test["model_answer_path"],
        "refWidth": test["ref_width"],
        "refHeight": test["ref_height"],
        "info": info,
        "fields": fields,
        "points": points,
    }


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
    folder_path = str(folder_path)
    with connect() as conn:
        conn.execute(
            "UPDATE tests SET student_folder_path = ? WHERE id = ?",
            (folder_path, test_id),
        )
        _set_test_info(conn, test_id, "生徒解答フォルダID", folder_path)
        conn.commit()


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
    skipped = 0
    errors: list[dict[str, str]] = []
    written_names: list[str] = []
    for r in rows:
        try:
            ok = append_result_row(
                test_id,
                r.get("fileName") or r.get("file_name") or "",
                r.get("sourcePath") or r.get("source_path") or "",
                r.get("warpedPath") or r.get("warped_path") or "",
                r.get("studentId") or r.get("student_id") or "",
                r.get("textMapping") or r.get("text_mapping") or {},
            )
            if ok:
                written += 1
                written_names.append(str(r.get("fileName") or ""))
            else:
                skipped += 1
        except Exception as e:
            errors.append({"fileName": str(r.get("fileName") or ""), "error": str(e)})
    return {
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "writtenFileNames": written_names,
    }


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
                "textMapping": texts,
                "judgments": judgments,
                "scores": scores,
                "warpedPath": r["warped_path"],
                "sourcePath": r["source_path"],
            }
        )
    return out


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


def export_results_to_excel(test_id: str, output_path: str) -> str:
    """採点結果を GAS 互換のワイド形式 Excel にエクスポート。"""
    import pandas as pd

    fields = get_answer_fields(test_id)
    preview = get_all_results(test_id)
    headers = ["生徒ID", "ファイル名", "ファイルID", "補正画像FileID", "氏名"]
    for f in fields:
        label = f["displayName"] or f["id"]
        headers.extend([f"{label}_テキスト", f"{label}_判定", f"{label}_得点"])

    rows = []
    for item in preview:
        row = {
            "生徒ID": item["studentId"],
            "ファイル名": item["fileName"],
            "ファイルID": item.get("sourcePath", ""),
            "補正画像FileID": item.get("warpedPath", ""),
            "氏名": "",
        }
        for f in fields:
            label = f["displayName"] or f["id"]
            fid = f["id"]
            row[f"{label}_テキスト"] = item["textMapping"].get(fid, "なし")
            row[f"{label}_判定"] = item.get("judgments", {}).get(fid, "")
            row[f"{label}_得点"] = item.get("scores", {}).get(fid, "")
        rows.append(row)

    df = pd.DataFrame(rows, columns=headers)
    df.to_excel(output_path, index=False, sheet_name="採点結果")
    return output_path
