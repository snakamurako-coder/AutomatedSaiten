"""⑦ 名簿管理・ID/氏名割当・外部連携得点。"""

from __future__ import annotations

import json
import re
import unicodedata
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


def _student_id_sort_key(row: dict[str, Any]) -> tuple:
    """4桁ID昇順用（数値として解釈できるものは数値順）。"""
    s = str(row.get("studentId") or "").strip()
    try:
        return (0, float(s), s)
    except ValueError:
        return (1, 0.0, s.lower())


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


def list_roster_absent_import_sources(exclude_test_id: str) -> list[dict[str, Any]]:
    """名簿・未受験者が保存済みの他テスト一覧（インポート元候補）。"""
    init_db()
    valid_rosters = set(list_roster_names())
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, test_name, last_saved_at, created_at
            FROM tests
            WHERE id != ?
            ORDER BY COALESCE(NULLIF(last_saved_at, ''), created_at) DESC
            """,
            (exclude_test_id,),
        ).fetchall()
    sources: list[dict[str, Any]] = []
    for row in rows:
        test_id = str(row["id"])
        roster_name = get_selected_roster_name(test_id).strip()
        if not roster_name or roster_name not in valid_rosters:
            continue
        absent_state = get_roster_absent_state(test_id)
        absent_roster = str(absent_state.get("rosterName") or "").strip()
        if absent_roster and absent_roster != roster_name:
            continue
        absent_students = list(absent_state.get("absentStudents") or [])
        if not absent_roster and not absent_students and not absent_state.get("savedAt"):
            continue
        roster_count = len(get_roster_rows(roster_name))
        if roster_count <= 0:
            continue
        sources.append(
            {
                "testId": test_id,
                "testName": row["test_name"] or test_id,
                "rosterName": roster_name,
                "absentCount": len(absent_students),
                "rosterCount": roster_count,
                "savedAt": absent_state.get("savedAt") or "",
            }
        )
    return sources


def import_roster_absent_from_test(target_test_id: str, source_test_id: str) -> dict[str, Any]:
    """他テストの選択名簿・未受験者設定を現在テストへコピーする。"""
    if target_test_id == source_test_id:
        raise ValueError("同じテストからはインポートできません。")
    roster_name = get_selected_roster_name(source_test_id).strip()
    if not roster_name:
        raise ValueError("コピー元に選択名簿がありません。")
    roster_rows = get_roster_rows(roster_name)
    if not roster_rows:
        raise ValueError(f"名簿「{roster_name}」が見つかりません。")
    absent_state = get_roster_absent_state(source_test_id)
    source_roster = str(absent_state.get("rosterName") or "").strip()
    if source_roster and source_roster != roster_name:
        raise ValueError("コピー元の名簿と未受験者設定が一致しません。")
    absent_students = list(absent_state.get("absentStudents") or [])
    save_selected_roster_name(target_test_id, roster_name)
    save_roster_absent_state(target_test_id, roster_name, absent_students)
    return {
        "rosterName": roster_name,
        "absentCount": len(absent_students),
        "rosterCount": len(roster_rows),
    }


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
    test_id: str,
    roster_name: str,
    absent_students: list[dict[str, str]],
    *,
    file_order: str = "asc",
) -> dict[str, Any]:
    """結果行をファイル名順、名簿を生徒ID昇順に並べて 1:1 で割り当てる。

    file_order:
      - \"asc\"  … ファイル名昇順（表スキャン、既定）
      - \"desc\" … ファイル名降順（裏スキャン用）
    名簿側は常に生徒4桁ID昇順。
    """
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
        key=_student_id_sort_key,
    )

    order = "DESC" if str(file_order or "").strip().lower() == "desc" else "ASC"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, file_name FROM results WHERE test_id = ? ORDER BY file_name {order}",
            (test_id,),
        ).fetchall()
        if len(rows) != len(attendees):
            raise ValueError(
                f"件数が一致しません: 回答 {len(rows)} 件 / 受験予定 {len(attendees)} 名。"
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
    return {
        "assigned": len(attendees),
        "skipped": False,
        "fileOrder": "desc" if order == "DESC" else "asc",
    }


def update_student_identity(
    test_id: str, result_id: int, student_id: str, name: str
) -> None:
    update_student_identities(test_id, [(result_id, student_id, name)])


def update_student_identities(
    test_id: str, rows: list[tuple[int, str, str]]
) -> None:
    """複数の解答用紙へ生徒ID・氏名を一度に書き戻す。"""
    if not rows:
        return
    with connect() as conn:
        for result_id, student_id, name in rows:
            conn.execute(
                "UPDATE results SET student_id = ?, name = ? WHERE id = ? AND test_id = ?",
                (str(student_id or ""), str(name or ""), int(result_id), test_id),
            )
        conn.commit()


def clear_result_identities(test_id: str) -> int:
    """答案の生徒ID・氏名だけを空にする。画像と採点は残す。"""
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE results SET student_id = '', name = '' WHERE test_id = ?",
            (test_id,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


# ==================== 外部連携得点 ====================

def _norm_person_name(value: str) -> str:
    """氏名比較用。空白と記号を除き、外字・異体字はそのまま残す。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    chars: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        category = unicodedata.category(ch)
        if category[:1] in {"P", "S", "C"}:
            continue
        chars.append(ch)
    return "".join(chars)


def _person_names_differ(feed_name: str, body_names: list[str]) -> bool:
    feed_norm = _norm_person_name(feed_name)
    body_norms = {_norm_person_name(name) for name in body_names}
    body_norms.discard("")
    if not feed_norm and not body_norms:
        return False
    return feed_norm not in body_norms


def _external_header_row(cells: list[str]) -> bool:
    if len(cells) < 3:
        return False
    try:
        float(str(cells[2]).replace("，", ""))
        return False
    except ValueError:
        pass
    head = "".join(cells[:3])
    return any(token in head for token in ("ID", "ＩＤ", "氏名", "得点", "名前"))


def parse_external_scores_csv(csv_text: str) -> list[dict[str, Any]]:
    """外部得点。列は ID, 氏名, 得点 の3列。照合の基準は ID。"""
    rows = []
    for ln in (csv_text or "").splitlines():
        if not ln.strip():
            continue
        cells = [c.strip() for c in re.split(r"[,;\t，]", ln)]
        if _external_header_row(cells):
            continue
        if len(cells) < 3 or not cells[0]:
            continue
        try:
            score = float(str(cells[2]).replace("，", ""))
        except ValueError:
            continue
        rows.append(
            {
                "studentId": cells[0],
                "name": cells[1],
                "score": score,
                "source": "CSV取込",
            }
        )
    return rows


def list_grading_identities(test_id: str) -> dict[str, dict[str, Any]]:
    """採点結果についている ID・氏名。⑬の修正を保存した内容が照合に出る。"""
    init_db()
    with connect() as conn:
        fetched = conn.execute(
            "SELECT student_id, name FROM results WHERE test_id = ? ORDER BY id",
            (test_id,),
        ).fetchall()
    source_rows = [
        {"studentId": row["student_id"], "name": row["name"]} for row in fetched
    ]
    by_id: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        sid = str(row.get("studentId") or "").strip()
        if not sid:
            continue
        name = str(row.get("name") or "").strip()
        slot = by_id.get(sid)
        if slot is None:
            by_id[sid] = {"studentId": sid, "name": name, "names": [name] if name else []}
            continue
        if name and name not in slot["names"]:
            slot["names"].append(name)
            if not slot["name"]:
                slot["name"] = name
    return by_id


def compare_external_scores(
    test_id: str, feed_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """外部の3列を、本体採点の ID・氏名と ID 基準で突き合わせる。"""
    grading = list_grading_identities(test_id)
    feed_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in feed_rows:
        sid = str(row.get("studentId") or "").strip()
        if not sid:
            continue
        feed_by_id.setdefault(sid, []).append(row)

    compared: list[dict[str, Any]] = []
    for sid, items in feed_by_id.items():
        chosen = items[-1]
        body = grading.get(sid)
        body_names = list(body["names"]) if body else []
        body_name = " / ".join(body_names)
        feed_name = str(chosen.get("name") or "").strip()
        duplicate = len(items) > 1
        name_differs = False
        if body is None:
            status = "本体にない"
        elif len(body_names) > 1:
            status = "本体の氏名が複数"
            name_differs = _person_names_differ(feed_name, body_names)
        elif _person_names_differ(feed_name, body_names):
            status = "氏名が違う"
            name_differs = True
        else:
            status = "一致"
        if duplicate and status == "一致":
            status = "IDが重複"
        elif duplicate:
            status = f"{status}・IDが重複"
        compared.append(
            {
                "studentId": sid,
                "bodyName": body_name,
                "feedName": feed_name,
                "score": chosen.get("score"),
                "status": status,
                "duplicateCount": len(items),
                "nameDiffers": name_differs,
            }
        )
    for sid, body in grading.items():
        if sid in feed_by_id:
            continue
        compared.append(
            {
                "studentId": sid,
                "bodyName": " / ".join(body["names"]),
                "feedName": "",
                "score": None,
                "status": "外部にない",
                "duplicateCount": 0,
                "nameDiffers": False,
            }
        )

    rank = {
        "氏名が違う": 0,
        "本体の氏名が複数": 1,
        "本体にない": 2,
        "外部にない": 3,
        "IDが重複": 4,
    }

    def sort_key(row: dict[str, Any]) -> tuple:
        status = str(row.get("status") or "")
        base = next((rank[key] for key in rank if key in status), 5)
        return (base, _student_id_sort_key({"studentId": row.get("studentId")}))

    compared.sort(key=sort_key)
    return compared


def import_external_scores(test_id: str, rows: list[dict[str, Any]]) -> int:
    """外部得点を追記し、結果行へ反映・総計点を再計算する。同一IDは後の行を採用する。"""
    if not rows:
        raise ValueError("取込対象の行がありません（形式: ID,氏名,得点）。")
    init_db()
    ordered: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("studentId") or "").strip()
        if not sid:
            continue
        ordered[sid] = row
    if not ordered:
        raise ValueError("取込対象の行がありません（形式: ID,氏名,得点）。")
    now = _now()
    with connect() as conn:
        conn.execute("DELETE FROM external_scores WHERE test_id = ?", (test_id,))
        for sid, row in ordered.items():
            conn.execute(
                """
                INSERT INTO external_scores(
                    test_id, student_id, student_name, score, source, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    sid,
                    str(row.get("name") or ""),
                    float(row.get("score") or 0),
                    str(row.get("source") or "CSV取込"),
                    now,
                ),
            )
        touch_progress_conn(conn, test_id, 7)
        conn.commit()

    from models.domain_repo import calculate_domain_scores

    calculate_domain_scores(test_id)
    return len(ordered)


def get_external_scores(test_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT student_id, student_name, score, source, imported_at FROM external_scores "
            "WHERE test_id = ? ORDER BY id",
            (test_id,),
        ).fetchall()
    return [
        {
            "studentId": r["student_id"],
            "name": r["student_name"] or "",
            "score": r["score"],
            "source": r["source"],
            "importedAt": r["imported_at"],
        }
        for r in rows
    ]
