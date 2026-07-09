"""採点基準（④）のデータ操作。"""

from __future__ import annotations

from collections import Counter
import json
from typing import Any

from models.database import connect, init_db
from models.test_repo import get_all_results, touch_progress_conn


def get_unique_answers(test_id: str, field_id: str) -> list[dict[str, Any]]:
    """OCR 結果から記述欄ごとのユニーク解答を集約。"""
    init_db()
    results = get_all_results(test_id)
    answers: list[str] = []
    for row in results:
        text = str(row.get("textMapping", {}).get(field_id, "") or "").strip()
        answers.append(text or "なし")

    counts = Counter(answers)
    items = [{"answer_text": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda x: (-x["count"], x["answer_text"]))
    return items


def get_grading_criteria(test_id: str, field_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if field_id:
            rows = conn.execute(
                """
                SELECT field_id, answer_text, judgment, score, reason, uniform_feedback_json
                FROM grading_criteria
                WHERE test_id = ? AND field_id = ?
                ORDER BY answer_text
                """,
                (test_id, field_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT field_id, answer_text, judgment, score, reason, uniform_feedback_json
                FROM grading_criteria
                WHERE test_id = ?
                ORDER BY field_id, answer_text
                """,
                (test_id,),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        uniform_feedback: dict[str, Any] | None = None
        raw = str(r["uniform_feedback_json"] or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    uniform_feedback = parsed
            except json.JSONDecodeError:
                uniform_feedback = None
        out.append(
            {
                "fieldId": r["field_id"],
                "answer_text": r["answer_text"],
                "judgment": r["judgment"],
                "score": int(r["score"]),
                "reason": r["reason"] or "",
                "uniform_feedback": uniform_feedback,
            }
        )
    return out


def get_criteria_grouped_by_field(test_id: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in get_grading_criteria(test_id):
        fid = str(rule["fieldId"])
        grouped.setdefault(fid, []).append(rule)
    return grouped


def save_grading_criteria(
    test_id: str,
    field_id: str,
    confirmed_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        conn.execute(
            "DELETE FROM grading_criteria WHERE test_id = ? AND field_id = ?",
            (test_id, field_id),
        )
        for rule in confirmed_rules or []:
            answer = str(rule.get("answer_text") or "").strip()
            if not answer:
                continue
            conn.execute(
                """
                INSERT INTO grading_criteria(
                    test_id, field_id, answer_text, judgment, score, reason, uniform_feedback_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    field_id,
                    answer,
                    str(rule.get("judgment") or "×"),
                    int(rule.get("score") or rule.get("recommended_score") or 0),
                    str(rule.get("reason") or ""),
                    json.dumps(rule.get("uniform_feedback"), ensure_ascii=False)
                    if isinstance(rule.get("uniform_feedback"), dict)
                    else "",
                ),
            )
        touch_progress_conn(conn, test_id, 4)
        conn.commit()
    return get_grading_criteria(test_id, field_id)


def merge_unique_with_criteria(
    test_id: str,
    field_id: str,
) -> list[dict[str, Any]]:
    """ユニーク解答一覧に保存済み基準をマージ。"""
    unique = get_unique_answers(test_id, field_id)
    saved = {r["answer_text"]: r for r in get_grading_criteria(test_id, field_id)}
    merged: list[dict[str, Any]] = []
    for item in unique:
        answer = item["answer_text"]
        base = saved.get(answer, {})
        merged.append(
            {
                "answer_text": answer,
                "count": item["count"],
                "judgment": base.get("judgment", ""),
                "score": base.get("score", ""),
                "reason": base.get("reason", ""),
                "uniform_feedback": base.get("uniform_feedback"),
                "deemed": False,
                "incorrect": False,
            }
        )
    for answer, rule in saved.items():
        if not any(m["answer_text"] == answer for m in merged):
            merged.append(
                {
                    "answer_text": answer,
                    "count": 0,
                    "judgment": rule.get("judgment", ""),
                    "score": rule.get("score", ""),
                    "reason": rule.get("reason", ""),
                    "uniform_feedback": rule.get("uniform_feedback"),
                    "deemed": False,
                    "incorrect": False,
                }
            )
    return merged


def save_uniform_feedback_config(
    test_id: str,
    field_id: str,
    answer_text: str,
    config: dict[str, Any] | None,
) -> None:
    init_db()
    answer = str(answer_text or "").strip()
    if not answer:
        return
    payload = (
        json.dumps(config, ensure_ascii=False)
        if isinstance(config, dict) and config
        else ""
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE grading_criteria
            SET uniform_feedback_json = ?
            WHERE test_id = ? AND field_id = ? AND answer_text = ?
            """,
            (payload, test_id, field_id, answer),
        )
        conn.commit()


def build_rule_map(test_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    rule_map: dict[str, dict[str, dict[str, Any]]] = {}
    for rule in get_grading_criteria(test_id):
        fid = str(rule["fieldId"])
        rule_map.setdefault(fid, {})[str(rule["answer_text"]).strip()] = {
            "judgment": rule["judgment"],
            "score": int(rule["score"]),
        }
    return rule_map


def get_field_answer_details(test_id: str, field_id: str) -> list[dict[str, Any]]:
    """記述欄ごとの生徒解答詳細（外れ値検出・画像表示用）。"""
    init_db()
    details: list[dict[str, Any]] = []
    for row in get_all_results(test_id):
        answer = str(row.get("textMapping", {}).get(field_id, "") or "").strip() or "なし"
        details.append(
            {
                "rowIndex": row["id"],
                "answer": answer,
                "answer_text": answer,
                "fileId": row.get("sourcePath") or row.get("warpedPath") or "",
                "fileName": row["fileName"],
                "studentId": row["studentId"],
                "studentName": row.get("name") or "",
                "warpedPath": row.get("warpedPath") or "",
            }
        )
    return details


def get_outlier_answer_groups(
    test_id: str,
    field_id: str,
    max_count: int = 2,
) -> list[dict[str, Any]]:
    max_count = max(1, int(max_count or 1))
    count_map: dict[str, dict[str, Any]] = {}
    for row in get_field_answer_details(test_id, field_id):
        answer = row["answer"]
        if answer not in count_map:
            count_map[answer] = {"answer_text": answer, "count": 0, "rows": []}
        count_map[answer]["count"] += 1
        count_map[answer]["rows"].append(
            {
                "rowIndex": row["rowIndex"],
                "studentId": row["studentId"],
                "fileName": row["fileName"],
                "fileId": row["fileId"],
                "warpedPath": row["warpedPath"],
            }
        )
    groups = [g for g in count_map.values() if g["count"] <= max_count]
    groups.sort(key=lambda g: (g["count"], g["answer_text"]))
    return groups


def get_answer_rows_for_pattern(
    test_id: str,
    field_id: str,
    answer_text: str,
) -> list[dict[str, Any]]:
    target = str(answer_text or "").strip() or "なし"
    return [
        {
            "rowIndex": row["rowIndex"],
            "studentId": row["studentId"],
            "fileName": row["fileName"],
            "fileId": row["fileId"],
            "warpedPath": row["warpedPath"],
            "studentName": row.get("studentName") or "",
            "answer_text": row["answer"],
        }
        for row in get_field_answer_details(test_id, field_id)
        if row["answer"] == target
    ]
