"""採点基準（④）のデータ操作。"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any

from models.database import connect, init_db
from models.grading_status import FINAL_JUDGMENTS, normalize_judgment
from models.test_repo import (
    get_all_results,
    get_answer_fields,
    get_points_conn,
    touch_progress_conn,
    update_results_field_grades,
)


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


def list_question_judgment_disagreements(test_id: str) -> list[dict[str, Any]]:
    """手動採点と採点基準で○△×が食い違う問いと、両者の判定数。

    両方に確定判定がある答案だけを数える。記号が1人でも違えばその問いを返す。
    """
    fields = get_answer_fields(test_id)
    criteria = get_criteria_grouped_by_field(test_id)
    results = list(get_all_results(test_id))
    out: list[dict[str, Any]] = []
    marks = ("○", "△", "×")
    for field in fields:
        fid = str(field.get("id") or "")
        if not fid:
            continue
        rules = {
            str(rule.get("answer_text") or ""): normalize_judgment(rule.get("judgment"))
            for rule in criteria.get(fid, [])
        }
        manual_counts: Counter[str] = Counter()
        auto_counts: Counter[str] = Counter()
        compared = 0
        mismatched = 0
        for row in results:
            ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
            manual = normalize_judgment((row.get("judgments") or {}).get(fid, ""))
            auto = rules.get(ans, "")
            if manual not in FINAL_JUDGMENTS or auto not in FINAL_JUDGMENTS:
                continue
            compared += 1
            manual_counts[manual] += 1
            auto_counts[auto] += 1
            if manual != auto:
                mismatched += 1
        if mismatched <= 0:
            continue
        out.append(
            {
                "field_id": fid,
                "display_name": str(field.get("displayName") or fid),
                "mismatch_count": mismatched,
                "compared_count": compared,
                "manual": {mark: int(manual_counts[mark]) for mark in marks},
                "auto": {mark: int(auto_counts[mark]) for mark in marks},
            }
        )
    return out


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


def existing_grade_disagrees(
    existing_judgment: Any,
    existing_score: Any,
    new_judgment: Any,
    new_score: Any,
) -> bool:
    """確定済みの手動判定が、書き込もうとする自動判定と違う。"""
    manual = normalize_judgment(existing_judgment)
    if manual not in FINAL_JUDGMENTS:
        return False
    auto = normalize_judgment(new_judgment)
    try:
        manual_score = int(existing_score or 0)
    except (TypeError, ValueError):
        manual_score = 0
    try:
        auto_score = int(new_score or 0)
    except (TypeError, ValueError):
        auto_score = 0
    return (manual, manual_score) != (auto, auto_score)


def apply_criteria_rules_to_results(
    test_id: str,
    field_id: str,
    rules: list[dict[str, Any]],
) -> int:
    """採点基準の判定・配点を results へ回答文字列単位で書き込む。

    すでに ○△× が付いていて基準と違う答案は上書きしない。
    """
    fid = str(field_id or "").strip()
    if not test_id or not fid or not rules:
        return 0
    by_answer: dict[str, tuple[str, int]] = {}
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
        by_answer[ans] = (judgment, score)
    if not by_answer:
        return 0
    grouped: dict[tuple[str, int], list[int]] = defaultdict(list)
    for row in get_all_results(test_id):
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        grade = by_answer.get(ans)
        if grade is None:
            continue
        judgment, score = grade
        if existing_grade_disagrees(
            (row.get("judgments") or {}).get(fid, ""),
            (row.get("scores") or {}).get(fid),
            judgment,
            score,
        ):
            continue
        rid = int(row.get("id") or 0)
        if rid > 0:
            grouped[(judgment, score)].append(rid)
    updated = 0
    for (judgment, score), ids in grouped.items():
        updated += update_results_field_grades(test_id, fid, ids, judgment, score)
    return updated


def apply_saved_criteria_to_field(test_id: str, field_id: str) -> int:
    """DB 保存済みの採点基準を、当該記述欄の手動採点結果へ反映。"""
    rules = get_grading_criteria(test_id, field_id)
    return apply_criteria_rules_to_results(test_id, field_id, rules)


def _field_max_score(test_id: str, field_id: str) -> int:
    with connect() as conn:
        pts = get_points_conn(conn, test_id)
    return max(1, int(pts.get(field_id, 1)))


def list_manual_criteria_mismatches(
    test_id: str,
    field_id: str,
    *,
    max_score: int | None = None,
) -> list[dict[str, Any]]:
    """手動採点と採点基準で判定・配点が異なる答案一覧。

    両方に確定判定（○△×）があり、正規化後の (判定, 配点) が違うものだけ。
    戻り値の各行は画像表示用キー（rowIndex 等）に加え、双方の判定・配点を含む。
    """
    init_db()
    fid = str(field_id or "").strip()
    if not test_id or not fid:
        return []
    cap = max(1, int(max_score)) if max_score is not None else _field_max_score(test_id, fid)
    rules = {
        str(r.get("answer_text") or ""): r for r in get_grading_criteria(test_id, fid)
    }
    out: list[dict[str, Any]] = []
    for row in get_all_results(test_id):
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        rule = rules.get(ans)
        if not rule:
            continue
        cj = normalize_judgment(rule.get("judgment"))
        if cj not in FINAL_JUDGMENTS:
            continue
        try:
            cs = int(rule.get("score") or 0)
        except (TypeError, ValueError):
            cs = 0
        cj, cs = _coerce_judgment_score(cj, cs, cap)

        mj = normalize_judgment((row.get("judgments") or {}).get(fid, ""))
        if mj not in FINAL_JUDGMENTS:
            continue
        try:
            ms = int((row.get("scores") or {}).get(fid, 0) or 0)
        except (TypeError, ValueError):
            ms = 0
        mj, ms = _coerce_judgment_score(mj, ms, cap)
        if (mj, ms) == (cj, cs):
            continue
        rid = int(row.get("id") or 0)
        if rid <= 0:
            continue
        out.append(
            {
                "rowIndex": rid,
                "studentId": row.get("studentId") or "",
                "fileName": row.get("fileName") or "",
                "fileId": row.get("sourcePath") or row.get("warpedPath") or "",
                "warpedPath": row.get("warpedPath") or "",
                "studentName": row.get("name") or "",
                "answer_text": ans,
                "manual_judgment": mj,
                "manual_score": ms,
                "criteria_judgment": cj,
                "criteria_score": cs,
            }
        )
    out.sort(
        key=lambda r: (
            str(r.get("answer_text") or ""),
            str(r.get("fileName") or ""),
            int(r.get("rowIndex") or 0),
        )
    )
    return out


def manual_criteria_mismatch_ids(
    test_id: str,
    field_id: str,
    *,
    max_score: int | None = None,
) -> set[int]:
    """不一致答案の result id 集合。"""
    return {
        int(r["rowIndex"])
        for r in list_manual_criteria_mismatches(test_id, field_id, max_score=max_score)
        if int(r.get("rowIndex") or 0) > 0
    }


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

    patterns: list[dict[str, Any]] = []
    for ans, counter in sorted(buckets.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        if not counter:
            continue
        ranked = counter.most_common()
        top_votes = ranked[0][1]
        leaders = [pair for pair, n in ranked if n == top_votes]
        tied = len(leaders) > 1
        chosen: tuple[str, int] | None = leaders[0] if not tied else None
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


_transfer_undo: dict[tuple[str, str], dict[str, Any]] = {}


def capture_grade_checkpoint(
    test_id: str,
    field_id: str,
    criteria_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """取込・反映の直前の採点基準と手動採点を保存する。"""
    fid = str(field_id or "").strip()
    source = criteria_rules if criteria_rules is not None else get_grading_criteria(test_id, fid)
    criteria: list[dict[str, Any]] = []
    for rule in source or []:
        judgment = str(rule.get("judgment") or "").strip()
        if judgment not in FINAL_JUDGMENTS and judgment not in ("○", "△", "×"):
            continue
        try:
            score = int(rule.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        judgment, score = _coerce_judgment_score(judgment, score)
        if judgment not in FINAL_JUDGMENTS:
            continue
        criteria.append(
            {
                "answer_text": str(rule.get("answer_text") or ""),
                "judgment": judgment,
                "score": score,
                "reason": rule.get("reason") or "",
                "uniform_feedback": rule.get("uniform_feedback")
                if isinstance(rule.get("uniform_feedback"), dict)
                else None,
            }
        )
    grades: list[dict[str, Any]] = []
    for row in get_all_results(test_id):
        rid = int(row.get("id") or 0)
        if rid <= 0:
            continue
        raw_j = (row.get("judgments") or {}).get(fid, "")
        try:
            raw_s = int((row.get("scores") or {}).get(fid, 0) or 0)
        except (TypeError, ValueError):
            raw_s = 0
        grades.append({"id": rid, "judgment": str(raw_j or ""), "score": raw_s})
    return {
        "test_id": str(test_id),
        "field_id": fid,
        "criteria": criteria,
        "grades": grades,
    }


def hold_grade_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    """保存を確定し、同じ記述欄の直前の取り消し地点を返す。"""
    key = (str(checkpoint.get("test_id") or ""), str(checkpoint.get("field_id") or ""))
    previous = _transfer_undo.get(key)
    _transfer_undo[key] = checkpoint
    return previous


def restore_checkpoint_slot(
    test_id: str,
    field_id: str,
    previous: dict[str, Any] | None,
) -> None:
    key = (str(test_id or ""), str(field_id or ""))
    if previous is None:
        _transfer_undo.pop(key, None)
    else:
        _transfer_undo[key] = previous


def peek_grade_checkpoint(test_id: str, field_id: str) -> dict[str, Any] | None:
    return _transfer_undo.get((str(test_id or ""), str(field_id or "")))


def pop_grade_checkpoint(test_id: str, field_id: str) -> dict[str, Any] | None:
    return _transfer_undo.pop((str(test_id or ""), str(field_id or "")), None)


def restore_grade_checkpoint(checkpoint: dict[str, Any]) -> None:
    """保存した採点基準と手動採点へ戻す。"""
    test_id = str(checkpoint.get("test_id") or "")
    fid = str(checkpoint.get("field_id") or "")
    save_grading_criteria(test_id, fid, list(checkpoint.get("criteria") or []))
    grouped: dict[tuple[str, int], list[int]] = defaultdict(list)
    for grade in checkpoint.get("grades") or []:
        rid = int(grade.get("id") or 0)
        if rid <= 0:
            continue
        judgment = str(grade.get("judgment") or "")
        try:
            score = int(grade.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        grouped[(judgment, score)].append(rid)
    for (judgment, score), ids in grouped.items():
        update_results_field_grades(test_id, fid, ids, judgment, score)


def manual_grade_decisions(test_id: str, field_id: str) -> list[dict[str, Any]]:
    """手動採点で確定した判定を、答案1件ずつ（結果ID順）返す。集計しない。"""
    fid = str(field_id or "").strip()
    decisions: list[dict[str, Any]] = []
    rows = list(get_all_results(test_id))
    rows.sort(key=lambda r: int(r.get("id") or 0))
    for row in rows:
        j = normalize_judgment((row.get("judgments") or {}).get(fid, ""))
        if j not in FINAL_JUDGMENTS:
            continue
        try:
            sc = int((row.get("scores") or {}).get(fid, 0) or 0)
        except (TypeError, ValueError):
            sc = 0
        j, sc = _coerce_judgment_score(j, sc)
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        decisions.append(
            {
                "result_id": int(row.get("id") or 0),
                "answer_text": ans,
                "judgment": j,
                "score": sc,
            }
        )
    return decisions


def import_manual_grades_into_criteria(
    test_id: str,
    field_id: str,
    *,
    only_missing: bool = False,
) -> dict[str, Any]:
    """手動採点の確定判定を、答案1件ずつ採点基準へ上書きする。

    同じ回答文字列に複数の判定があっても集計しない。後の一件が基準を置き換える。
    results へは書き戻さない。reason / uniform_feedback は維持する。
    only_missing=True のときは、既に判定がある回答文字列は置き換えない。
    """
    decisions = manual_grade_decisions(test_id, field_id)
    if not decisions:
        return {"saved_count": 0, "decision_count": 0, "rules": []}

    fid = str(field_id or "").strip()
    merged = merge_unique_with_criteria(test_id, fid)
    by_ans = {str(r.get("answer_text") or ""): r for r in merged}
    written: set[str] = set()
    for dec in decisions:
        ans = str(dec["answer_text"])
        row = by_ans.get(ans)
        if row is None:
            row = {
                "answer_text": ans,
                "count": 0,
                "judgment": "",
                "score": "",
                "reason": "",
                "uniform_feedback": None,
            }
            by_ans[ans] = row
            merged.append(row)
        existing_j = normalize_judgment(row.get("judgment"))
        if only_missing and existing_j in FINAL_JUDGMENTS:
            continue
        row["judgment"] = dec["judgment"]
        row["score"] = dec["score"]
        written.add(ans)

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
    save_grading_criteria(test_id, fid, rules)
    return {
        "saved_count": len(written),
        "decision_count": len(decisions),
        "rules": rules,
    }


def reflect_criterion_to_results(
    test_id: str,
    field_id: str,
    answer_text: str,
    judgment: str,
    score: int,
    *,
    max_score: int = 99,
) -> dict[str, Any]:
    """採点基準の1件を保存し、同じ回答文字列の手動採点へ即反映する。"""
    ans = str(answer_text or "").strip() or "なし"
    j, sc = _coerce_judgment_score(judgment, score, max_score)
    if j not in FINAL_JUDGMENTS or not ans:
        return {"judgment": j, "score": sc, "result_count": 0}
    fid = str(field_id or "").strip()
    existing = {
        str(r.get("answer_text") or ""): dict(r)
        for r in get_grading_criteria(test_id, fid)
    }
    prev = existing.get(ans) or {
        "answer_text": ans,
        "reason": "",
        "uniform_feedback": None,
    }
    prev["answer_text"] = ans
    prev["judgment"] = j
    prev["score"] = sc
    existing[ans] = prev
    rules: list[dict[str, Any]] = []
    for row in existing.values():
        judgment_s = str(row.get("judgment") or "").strip()
        if judgment_s not in FINAL_JUDGMENTS and judgment_s not in ("○", "△", "×"):
            continue
        try:
            score_i = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score_i = 0
        judgment_s, score_i = _coerce_judgment_score(judgment_s, score_i, max_score)
        if judgment_s not in FINAL_JUDGMENTS:
            continue
        rules.append(
            {
                "answer_text": row.get("answer_text") or "",
                "judgment": judgment_s,
                "score": score_i,
                "reason": row.get("reason") or "",
                "uniform_feedback": row.get("uniform_feedback")
                if isinstance(row.get("uniform_feedback"), dict)
                else None,
            }
        )
    save_grading_criteria(test_id, fid, rules)
    result_count = apply_criteria_rules_to_results(
        test_id,
        fid,
        [{"answer_text": ans, "judgment": j, "score": sc}],
    )
    return {"judgment": j, "score": sc, "result_count": result_count}


def sync_committed_grades_to_criteria(
    test_id: str,
    field_id: str,
    result_ids: list[int],
    judgment: str,
    score: int,
    *,
    max_score: int = 99,
    propagate_to_results: bool = True,
) -> dict[str, Any]:
    """手動採点で確定した判定を採点基準へ即時反映する。

    既定では同じ OCR テキストの全 results にも波及する。
    propagate_to_results=False のときは採点基準のみ更新する。

    未判定・保留は変更しない。
    """
    j, sc = _coerce_judgment_score(judgment, score, max_score)
    if j not in FINAL_JUDGMENTS:
        return {"pattern_count": 0, "result_count": 0, "answers": set()}
    id_set = {int(x) for x in result_ids if int(x or 0) > 0}
    if not id_set:
        return {"pattern_count": 0, "result_count": 0, "answers": set()}
    fid = str(field_id or "").strip()
    answers: set[str] = set()
    for row in get_all_results(test_id):
        rid = int(row.get("id") or 0)
        if rid not in id_set:
            continue
        ans = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        answers.add(ans)
    if not answers:
        return {"pattern_count": 0, "result_count": 0, "answers": set()}

    # 既存基準を落さないよう、保存済み＋今回分をマージして書き戻す
    existing = {
        str(r.get("answer_text") or ""): dict(r)
        for r in get_grading_criteria(test_id, fid)
    }
    for ans in answers:
        prev = existing.get(ans) or {
            "answer_text": ans,
            "judgment": j,
            "score": sc,
            "reason": "",
            "uniform_feedback": None,
        }
        prev["answer_text"] = ans
        prev["judgment"] = j
        prev["score"] = sc
        existing[ans] = prev

    rules: list[dict[str, Any]] = []
    for row in existing.values():
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
    result_count = 0
    if propagate_to_results:
        propagate_rules = [
            {"answer_text": ans, "judgment": j, "score": sc} for ans in answers
        ]
        result_count = apply_criteria_rules_to_results(test_id, fid, propagate_rules)
    return {
        "pattern_count": len(answers),
        "result_count": result_count,
        "answers": answers,
        "judgment": j,
        "score": sc,
    }


def drop_answer_criterion_if_no_final(
    test_id: str,
    field_id: str,
    answer_text: str,
) -> bool:
    """この表記を○△×付きで持つ答案が無くなったら、採点基準から外す。

    OCRを切り分けて元の文字列から離れたあと、残った未採点へ
    昔の判定が波及しないようにする。
    """
    fid = str(field_id or "").strip()
    ans = str(answer_text or "").strip() or "なし"
    if not fid:
        return False
    for row in get_all_results(test_id):
        text = str((row.get("textMapping") or {}).get(fid, "") or "").strip() or "なし"
        if text != ans:
            continue
        if normalize_judgment((row.get("judgments") or {}).get(fid, "")) in FINAL_JUDGMENTS:
            return False
    rules = [
        r
        for r in get_grading_criteria(test_id, fid)
        if (str(r.get("answer_text") or "").strip() or "なし") != ans
    ]
    current = get_grading_criteria(test_id, fid)
    if len(rules) == len(current):
        return False
    save_grading_criteria(test_id, fid, rules)
    return True


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
