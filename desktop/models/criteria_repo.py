"""採点基準（④）のデータ操作。"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any

from models.database import connect, init_db
from models.grading_status import FINAL_JUDGMENTS, normalize_judgment
from models.test_repo import get_all_results, touch_progress_conn, update_results_field_grades


_JUDGMENT_RANK = {"○": 0, "△": 1, "×": 2}


def _coerce_judgment_score(judgment: str, score: int, max_score: int = 99) -> tuple[str, int]:
    """×は0点、○/△は0点不可。"""
    j = normalize_judgment(judgment)
    if j not in FINAL_JUDGMENTS:
        j = str(judgment or "").strip()
    cap = max(1, int(max_score))
    try:
        sc = int(score)
    except (TypeError, ValueError):
        sc = 0
    sc = max(0, min(cap, sc))
    if j == "×":
        return j, 0
    if j == "○":
        return j, (cap if sc <= 0 else sc)
    if j == "△":
        if sc <= 0:
            sc = 1
        return j, sc
    return j, sc


def criteria_display_sort_key(row: dict[str, Any]) -> tuple:
    """採点基準の表示順: ○ → △ → ×、同一判定は配点の高い順。"""
    judgment = str(row.get("judgment") or "").strip()
    rank = _JUDGMENT_RANK.get(judgment, 8 if judgment else 9)
    raw_score = row.get("score")
    try:
        score = int(raw_score) if raw_score not in ("", None) else -(10**9)
    except (TypeError, ValueError):
        score = -(10**9)
    return (
        rank,
        -score,
        -int(row.get("count") or 0),
        str(row.get("answer_text") or ""),
    )


def sort_criteria_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(key=criteria_display_sort_key)
    return rows


def get_unique_answers(test_id: str, field_id: str) -> list[dict[str, Any]]:
    """OCR 結果から記述欄ごとのユニーク回答を集約。"""
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


def get_deemed_merged_sources(test_id: str, field_id: str) -> set[str]:
    """みなし採点で正答に統合済みの旧回答（OCR 上の文字列）。"""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT source_answer
            FROM deemed_scoring
            WHERE test_id = ? AND field_id = ?
            """,
            (test_id, field_id),
        ).fetchall()
    return {
        str(r["source_answer"] or "").strip()
        for r in rows
        if str(r["source_answer"] or "").strip()
    }


def delete_grading_criteria_answers(
    test_id: str,
    field_id: str,
    answer_texts: list[str],
) -> None:
    """指定回答の採点基準行を削除（みなし統合後の旧回答除去用）。"""
    init_db()
    answers = [str(a).strip() for a in answer_texts if str(a).strip()]
    if not answers:
        return
    placeholders = ",".join("?" * len(answers))
    with connect() as conn:
        conn.execute(
            f"""
            DELETE FROM grading_criteria
            WHERE test_id = ? AND field_id = ? AND answer_text IN ({placeholders})
            """,
            (test_id, field_id, *answers),
        )
        conn.commit()


def merge_unique_with_criteria(
    test_id: str,
    field_id: str,
) -> list[dict[str, Any]]:
    """ユニーク回答一覧に保存済み基準をマージ。"""
    unique = get_unique_answers(test_id, field_id)
    saved = {r["answer_text"]: r for r in get_grading_criteria(test_id, field_id)}
    deemed_sources = get_deemed_merged_sources(test_id, field_id)
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
        if answer in deemed_sources:
            continue
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
    return sort_criteria_rows(merged)


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


def result_ids_for_answers(
    test_id: str,
    field_id: str,
    answers: set[str],
) -> list[int]:
    """回答文字列に一致する答案 ID。"""
    if not answers:
        return []
    fid = str(field_id or "")
    ids: list[int] = []
    for row in get_all_results(test_id):
        text = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        if text not in answers:
            continue
        rid = int(row.get("id") or 0)
        if rid > 0:
            ids.append(rid)
    return ids


def apply_criteria_rules_to_results(
    test_id: str,
    field_id: str,
    rules: list[dict[str, Any]],
) -> int:
    """採点基準の判定・配点を results（手動採点）へ回答文字列単位で書き込む。"""
    fid = str(field_id or "").strip()
    if not test_id or not fid or not rules:
        return 0
    by_grade: dict[tuple[str, int], set[str]] = {}
    for rule in rules:
        ans = str(rule.get("answer_text") or "").strip() or "なし"
        judgment = normalize_judgment(rule.get("judgment"))
        if judgment not in FINAL_JUDGMENTS:
            continue
        try:
            score = int(rule.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        judgment, score = _coerce_judgment_score(judgment, score)
        by_grade.setdefault((judgment, score), set()).add(ans)
    updated = 0
    for (judgment, score), answers in by_grade.items():
        ids = result_ids_for_answers(test_id, fid, answers)
        if not ids:
            continue
        updated += update_results_field_grades(test_id, fid, ids, judgment, score)
    return updated


def apply_saved_criteria_to_field(test_id: str, field_id: str) -> int:
    """DB 保存済みの採点基準を、当該記述欄の手動採点結果へ反映。"""
    rules = get_grading_criteria(test_id, field_id)
    return apply_criteria_rules_to_results(test_id, field_id, rules)


def aggregate_manual_grades_by_answer(
    test_id: str,
    field_id: str,
) -> dict[str, Any]:
    """手動採点の判定を回答文字列ごとに多数決で集約（○△×のみ。?・未採点は票外）。"""
    init_db()
    fid = str(field_id or "").strip()
    buckets: dict[str, Counter[tuple[str, int]]] = defaultdict(Counter)
    totals: dict[str, int] = defaultdict(int)
    for row in get_all_results(test_id):
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        totals[ans] += 1
        j = normalize_judgment((row.get("judgments") or {}).get(fid, ""))
        if j not in FINAL_JUDGMENTS:
            continue
        try:
            sc = int((row.get("scores") or {}).get(fid, 0) or 0)
        except (TypeError, ValueError):
            sc = 0
        j, sc = _coerce_judgment_score(j, sc)
        buckets[ans][(j, sc)] += 1

    existing = {
        str(r.get("answer_text") or ""): r for r in get_grading_criteria(test_id, fid)
    }
    patterns: list[dict[str, Any]] = []
    for ans, counter in sorted(buckets.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        if not counter:
            continue
        ranked = counter.most_common()
        top_votes = ranked[0][1]
        leaders = [pair for pair, n in ranked if n == top_votes]
        tied = len(leaders) > 1
        chosen: tuple[str, int] | None = None
        if not tied:
            chosen = leaders[0]
        else:
            prev = existing.get(ans)
            if prev:
                pj = normalize_judgment(prev.get("judgment"))
                try:
                    ps = int(prev.get("score") or 0)
                except (TypeError, ValueError):
                    ps = 0
                if pj in FINAL_JUDGMENTS:
                    pj, ps = _coerce_judgment_score(pj, ps)
                    if (pj, ps) in leaders:
                        chosen = (pj, ps)
                        tied = False
        vote_summary = " / ".join(
            f"{j}{sc}点×{n}" for (j, sc), n in ranked[:4]
        )
        patterns.append(
            {
                "answer_text": ans,
                "judgment": chosen[0] if chosen else "",
                "score": chosen[1] if chosen else "",
                "votes": top_votes,
                "total_count": int(totals.get(ans, 0)),
                "graded_count": int(sum(counter.values())),
                "tied": tied,
                "vote_summary": vote_summary,
                "has_winner": chosen is not None,
            }
        )

    winners = [p for p in patterns if p.get("has_winner")]
    tied_list = [p for p in patterns if p.get("tied")]
    return {
        "patterns": patterns,
        "winners": winners,
        "tied": tied_list,
        "winner_count": len(winners),
        "tied_count": len(tied_list),
    }


def import_manual_grades_into_criteria(
    test_id: str,
    field_id: str,
) -> dict[str, Any]:
    """手動採点の多数決を採点基準へ保存（results へは書き戻さない）。

    reason / uniform_feedback は既存基準を維持。票割れで未決定の回答は既存を残す。
    """
    agg = aggregate_manual_grades_by_answer(test_id, field_id)
    winners = {
        str(p["answer_text"]): p for p in agg["winners"] if p.get("has_winner")
    }
    if not winners:
        return {**agg, "saved_count": 0, "rules": []}

    # 現在のユニーク回答＋既存基準を土台に、勝者だけ判定を上書き
    merged = merge_unique_with_criteria(test_id, field_id)
    by_ans = {str(r.get("answer_text") or ""): r for r in merged}
    for ans, win in winners.items():
        row = by_ans.get(ans)
        if row is None:
            row = {
                "answer_text": ans,
                "count": int(win.get("total_count") or 0),
                "judgment": "",
                "score": "",
                "reason": "",
                "uniform_feedback": None,
            }
            by_ans[ans] = row
            merged.append(row)
        row["judgment"] = win["judgment"]
        row["score"] = win["score"]

    rules: list[dict[str, Any]] = []
    for row in merged:
        judgment = str(row.get("judgment") or "").strip()
        if not judgment:
            continue
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        judgment, score = _coerce_judgment_score(judgment, score)
        rules.append(
            {
                "answer_text": row["answer_text"],
                "judgment": judgment,
                "score": score,
                "reason": row.get("reason") or "",
                "uniform_feedback": row.get("uniform_feedback")
                if isinstance(row.get("uniform_feedback"), dict)
                else None,
            }
        )
    save_grading_criteria(test_id, field_id, rules)
    return {**agg, "saved_count": len(winners), "rules": rules}


def sync_committed_grades_to_criteria(
    test_id: str,
    field_id: str,
    result_ids: list[int],
    judgment: str,
    score: int,
    *,
    max_score: int = 99,
) -> int:
    """手動採点で確定した判定を、対象答案の回答文字列の採点基準へ即時反映。

    未判定・保留は基準を変更しない。戻り値は更新した回答パターン数。
    """
    j, sc = _coerce_judgment_score(judgment, score, max_score)
    if j not in FINAL_JUDGMENTS:
        return 0
    id_set = {int(x) for x in result_ids if int(x or 0) > 0}
    if not id_set:
        return 0
    fid = str(field_id or "").strip()
    answers: set[str] = set()
    for row in get_all_results(test_id):
        rid = int(row.get("id") or 0)
        if rid not in id_set:
            continue
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        answers.add(ans)
    if not answers:
        return 0

    merged = merge_unique_with_criteria(test_id, fid)
    by_ans = {str(r.get("answer_text") or ""): r for r in merged}
    for ans in answers:
        row = by_ans.get(ans)
        if row is None:
            row = {
                "answer_text": ans,
                "count": 0,
                "judgment": j,
                "score": sc,
                "reason": "",
                "uniform_feedback": None,
            }
            by_ans[ans] = row
            merged.append(row)
        else:
            row["judgment"] = j
            row["score"] = sc

    rules: list[dict[str, Any]] = []
    for row in merged:
        judgment_s = str(row.get("judgment") or "").strip()
        if not judgment_s:
            continue
        try:
            score_i = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score_i = 0
        judgment_s, score_i = _coerce_judgment_score(judgment_s, score_i, max_score)
        rules.append(
            {
                "answer_text": row["answer_text"],
                "judgment": judgment_s,
                "score": score_i,
                "reason": row.get("reason") or "",
                "uniform_feedback": row.get("uniform_feedback")
                if isinstance(row.get("uniform_feedback"), dict)
                else None,
            }
        )
    save_grading_criteria(test_id, fid, rules)
    return len(answers)


def get_field_answer_details(test_id: str, field_id: str) -> list[dict[str, Any]]:
    """記述欄ごとの生徒回答詳細（外れ値検出・画像表示用）。"""
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
