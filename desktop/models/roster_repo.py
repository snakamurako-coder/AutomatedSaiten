"""⑦ 名簿管理・ID/氏名割当・外部連携得点。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from models.database import connect, init_db
from models.test_repo import _set_test_info, touch_progress_conn

ROSTER_MAPPING_FIELDS = [
    ("studentId", "ID"),
    ("year", "年"),
    ("classNo", "組"),
    ("number", "番号"),
    ("name", "氏名"),
    ("attr1", "その他属性1"),
    ("attr2", "その他属性2"),
    ("attr3", "その他属性3"),
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 名簿 CRUD ====================

def list_roster_names() -> list[str]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT roster_name FROM roster ORDER BY roster_name"
        ).fetchall()
    return [r["roster_name"] for r in rows]


def get_roster_rows(roster_name: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM roster WHERE roster_name = ? ORDER BY id",
            (roster_name,),
        ).fetchall()
    return [
        {
            "rosterName": r["roster_name"],
            "studentId": r["student_id"],
            "year": r["year"] or "",
            "classNo": r["class_name"] or "",
            "number": r["number"] or "",
            "name": r["student_name"] or "",
            "attr1": r["attr1"] or "",
            "attr2": r["attr2"] or "",
            "attr3": r["attr3"] or "",
        }
        for r in rows
    ]


def save_roster_rows(roster_name: str, rows: list[dict[str, Any]]) -> int:
    roster_name = (roster_name or "").strip()
    if not roster_name:
        raise ValueError("名簿名を入力してください。")
    if not rows:
        raise ValueError("名簿データが空です。")
    with connect() as conn:
        conn.execute("DELETE FROM roster WHERE roster_name = ?", (roster_name,))
        for r in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO roster(
                    roster_name, student_id, year, class_name, number,
                    student_name, attr1, attr2, attr3
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    roster_name,
                    str(r.get("studentId") or "").strip(),
                    str(r.get("year") or ""),
                    str(r.get("classNo") or ""),
                    str(r.get("number") or ""),
                    str(r.get("name") or ""),
                    str(r.get("attr1") or ""),
                    str(r.get("attr2") or ""),
                    str(r.get("attr3") or ""),
                ),
            )
        conn.commit()
    return len(rows)


def parse_roster_tsv(tsv_text: str) -> dict[str, Any]:
    """TSV（またはカンマ区切り）テキストを行列に分解する。"""
    lines = [ln for ln in (tsv_text or "").splitlines() if ln.strip()]
    rows: list[list[str]] = []
    for ln in lines:
        cells = ln.split("\t") if "\t" in ln else re.split(r"[,;]", ln)
        rows.append([c.strip() for c in cells])
    col_count = max((len(r) for r in rows), default=0)
    return {"rows": rows, "colCount": col_count, "previewRows": rows[:8]}


def import_roster_with_mapping(
    roster_name: str,
    raw_rows: list[list[str]],
    mapping: dict[int, str],
    *,
    skip_first_row: bool = False,
) -> int:
    """列マッピング {列index: フィールドキー} に従って名簿を登録する。"""
    rows = raw_rows[1:] if skip_first_row else raw_rows
    parsed = []
    for cells in rows:
        rec: dict[str, str] = {}
        for col_idx, key in mapping.items():
            if not key or key == "ignore":
                continue
            if 0 <= col_idx < len(cells):
                rec[key] = cells[col_idx]
        if rec.get("studentId") or rec.get("name"):
            parsed.append(rec)
    if not parsed:
        raise ValueError("有効な行がありません（ID または氏名の列を指定してください）。")
    return save_roster_rows(roster_name, parsed)


def _roster_sort_key(row: dict[str, Any]) -> tuple:
    def num(v: str) -> tuple[int, Any]:
        s = str(v or "").strip()
        try:
            return (0, float(s))
        except ValueError:
            return (1, s)

    return (num(row.get("classNo", "")), num(row.get("number", "")), num(row.get("studentId", "")))


# ==================== 選択名簿・未受験者（test_info） ====================

def save_selected_roster_name(test_id: str, roster_name: str) -> None:
    with connect() as conn:
        _set_test_info(conn, test_id, "選択名簿名", roster_name or "")
        conn.execute(
            "UPDATE tests SET selected_roster = ? WHERE id = ?", (roster_name or "", test_id)
        )
        conn.commit()


def save_roster_absent_state(
    test_id: str, roster_name: str, absent_students: list[dict[str, str]]
) -> None:
    payload = {
        "rosterName": roster_name or "",
        "absentStudents": absent_students or [],
        "savedAt": _now(),
    }
    with connect() as conn:
        _set_test_info(conn, test_id, "未受験者", json.dumps(payload, ensure_ascii=False))
        conn.commit()


def get_roster_absent_state(test_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM test_info WHERE test_id = ? AND key = '未受験者'",
            (test_id,),
        ).fetchone()
    if not row or not row["value"]:
        return {"rosterName": "", "absentStudents": [], "savedAt": ""}
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return {"rosterName": "", "absentStudents": [], "savedAt": ""}


def get_selected_roster_name(test_id: str) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM test_info WHERE test_id = ? AND key = '選択名簿名'",
            (test_id,),
        ).fetchone()
    return (row["value"] if row else "") or ""


# ==================== ID・氏名割当 ====================

def get_id_assignment_status(test_id: str) -> dict[str, Any]:
    with connect() as conn:
        use_row = conn.execute(
            "SELECT use_id_mark FROM tests WHERE id = ?", (test_id,)
        ).fetchone()
        use_id_mark = bool(use_row and use_row["use_id_mark"])
        rows = conn.execute(
            "SELECT student_id FROM results WHERE test_id = ?", (test_id,)
        ).fetchall()
    result_count = len(rows)
    with_id = sum(
        1
        for r in rows
        if str(r["student_id"] or "").strip() and "?" not in str(r["student_id"])
    )
    skip = use_id_mark and result_count > 0 and with_id > result_count / 2
    return {
        "useOmrIdMark": use_id_mark,
        "resultCount": result_count,
        "withIdCount": with_id,
        "skipAssignment": skip,
        "selectedRosterName": get_selected_roster_name(test_id),
    }


def _absent_key_set(absent_students: list[dict[str, str]]) -> tuple[set, set]:
    ids = {str(a.get("studentId") or "").strip() for a in absent_students if a.get("studentId")}
    names = {str(a.get("name") or "").strip() for a in absent_students if a.get("name")}
    return ids, names


def get_roster_assignment_preview(
    test_id: str, roster_name: str, absent_students: list[dict[str, str]]
) -> dict[str, Any]:
    roster = get_roster_rows(roster_name)
    absent_ids, absent_names = _absent_key_set(absent_students or [])
    attendees = [
        r
        for r in roster
        if r["studentId"] not in absent_ids and r["name"] not in absent_names
    ]
    with connect() as conn:
        result_count = conn.execute(
            "SELECT COUNT(*) AS c FROM results WHERE test_id = ?", (test_id,)
        ).fetchone()["c"]
    return {
        "rosterCount": len(roster),
        "absentCount": len(roster) - len(attendees),
        "expectedCount": len(attendees),
        "resultCount": result_count,
        "match": len(attendees) == result_count,
    }


def assign_ids_from_roster(
    test_id: str, roster_name: str, absent_students: list[dict[str, str]]
) -> dict[str, Any]:
    """補正画像（結果行）をファイル名順、名簿を組・番号順に並べて 1:1 で割り当てる。"""
    status = get_id_assignment_status(test_id)
    if status["skipAssignment"]:
        return {"assigned": 0, "skipped": True, "message": "IDマーク欄から取得済みのためスキップしました。"}

    roster = get_roster_rows(roster_name)
    if not roster:
        raise ValueError(f"名簿「{roster_name}」にデータがありません。")
    absent_ids, absent_names = _absent_key_set(absent_students or [])
    attendees = sorted(
        (
            r
            for r in roster
            if r["studentId"] not in absent_ids and r["name"] not in absent_names
        ),
        key=_roster_sort_key,
    )

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, file_name FROM results WHERE test_id = ? ORDER BY file_name",
            (test_id,),
        ).fetchall()
        if len(rows) != len(attendees):
            raise ValueError(
                f"件数が一致しません: 解答 {len(rows)} 件 / 受験予定 {len(attendees)} 名。"
                "未受験者の指定を確認してください。"
            )
        for row, student in zip(rows, attendees):
            conn.execute(
                "UPDATE results SET student_id = ?, name = ? WHERE id = ?",
                (student["studentId"], student["name"], row["id"]),
            )
        touch_progress_conn(conn, test_id, 7)
        conn.commit()

    save_selected_roster_name(test_id, roster_name)
    save_roster_absent_state(test_id, roster_name, absent_students or [])
    return {"assigned": len(attendees), "skipped": False}


def update_student_identity(
    test_id: str, result_id: int, student_id: str, name: str
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE results SET student_id = ?, name = ? WHERE id = ? AND test_id = ?",
            (str(student_id or ""), str(name or ""), result_id, test_id),
        )
        conn.commit()


# ==================== 外部連携得点 ====================

def parse_external_scores_csv(csv_text: str) -> list[dict[str, Any]]:
    rows = []
    for ln in (csv_text or "").splitlines():
        if not ln.strip():
            continue
        cells = [c.strip() for c in re.split(r"[,;\t]", ln)]
        if len(cells) < 2:
            continue
        try:
            score = float(cells[1])
        except ValueError:
            continue  # ヘッダー行など数値でない行はスキップ
        rows.append(
            {
                "studentId": cells[0],
                "score": score,
                "source": cells[2] if len(cells) > 2 and cells[2] else "CSV取込",
            }
        )
    return rows


def import_external_scores(test_id: str, rows: list[dict[str, Any]]) -> int:
    """外部得点を追記し、結果行へ反映・総計点を再計算する。"""
    if not rows:
        raise ValueError("取込対象の行がありません（形式: 生徒ID,得点[,ソース]）。")
    now = _now()
    with connect() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO external_scores(test_id, student_id, score, source, imported_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (test_id, str(r["studentId"]), float(r["score"]), str(r.get("source") or "CSV取込"), now),
            )
        touch_progress_conn(conn, test_id, 7)
        conn.commit()

    from models.domain_repo import calculate_domain_scores

    calculate_domain_scores(test_id)
    return len(rows)


def get_external_scores(test_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT student_id, score, source, imported_at FROM external_scores "
            "WHERE test_id = ? ORDER BY id",
            (test_id,),
        ).fetchall()
    return [
        {
            "studentId": r["student_id"],
            "score": r["score"],
            "source": r["source"],
            "importedAt": r["imported_at"],
        }
        for r in rows
    ]
