"""OCR 置換・みなし採点（④ の前処理）。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from models.criteria_repo import get_unique_answers
from models.database import connect, init_db
from models.test_repo import get_all_results, rewrite_field_texts


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def apply_replacement_rules(text: str, rules: list[dict[str, Any]]) -> str:
    """GAS applyReplacementRules_ 相当。"""
    result = str(text or "")
    for rule in rules or []:
        search = str(rule.get("search") or "")
        if not search:
            continue
        replace = str(rule.get("replace") if rule.get("replace") is not None else "")
        if rule.get("useRegex") or rule.get("use_regex"):
            try:
                result = re.sub(search, replace, result, flags=re.IGNORECASE)
            except re.error:
                continue
        else:
            result = result.replace(search, replace)
    result = result.strip()
    return result or "なし"


def get_ocr_replacements(test_id: str, field_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if field_id:
            rows = conn.execute(
                """
                SELECT field_id, search_text, replace_text, use_regex
                FROM ocr_replacements
                WHERE test_id = ? AND field_id = ?
                ORDER BY search_text
                """,
                (test_id, field_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT field_id, search_text, replace_text, use_regex
                FROM ocr_replacements
                WHERE test_id = ?
                ORDER BY field_id, search_text
                """,
                (test_id,),
            ).fetchall()
    return [
        {
            "fieldId": r["field_id"],
            "search": r["search_text"],
            "replace": r["replace_text"] or "",
            "useRegex": bool(r["use_regex"]),
        }
        for r in rows
    ]


def save_ocr_replacements(
    test_id: str,
    field_id: str,
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        conn.execute(
            "DELETE FROM ocr_replacements WHERE test_id = ? AND field_id = ?",
            (test_id, field_id),
        )
        for rule in rules or []:
            search = str(rule.get("search") or "").strip()
            if not search:
                continue
            conn.execute(
                """
                INSERT INTO ocr_replacements(
                    test_id, field_id, search_text, replace_text, use_regex
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    field_id,
                    search,
                    str(rule.get("replace") if rule.get("replace") is not None else ""),
                    1 if rule.get("useRegex") or rule.get("use_regex") else 0,
                ),
            )
        conn.commit()
    return get_ocr_replacements(test_id, field_id)


def apply_text_replacements_to_field(
    test_id: str,
    field_id: str,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if rules:
        save_ocr_replacements(test_id, field_id, rules)
    else:
        rules = get_ocr_replacements(test_id, field_id)

    if not rules:
        return {"answers": get_unique_answers(test_id, field_id), "replacedCount": 0}

    replaced_count = 0

    def transform(old: str) -> str:
        nonlocal replaced_count
        old_norm = str(old or "").strip() or "なし"
        new_val = apply_replacement_rules(old, rules)
        if old_norm != new_val:
            replaced_count += 1
        return new_val

    rewrite_field_texts(test_id, field_id, lambda _old: True, transform, transform_mode=True)
    return {
        "answers": get_unique_answers(test_id, field_id),
        "replacedCount": replaced_count,
    }


def get_deemed_draft(test_id: str, field_id: str) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT canonical, source_answer
            FROM deemed_draft
            WHERE test_id = ? AND field_id = ?
            """,
            (test_id, field_id),
        ).fetchall()
    canonical = ""
    sources: list[str] = []
    for r in rows:
        if not canonical:
            canonical = str(r["canonical"] or "")
        src = str(r["source_answer"] or "").strip()
        if src:
            sources.append(src)
    return {"canonical": canonical, "sources": sources}


def save_deemed_scoring_draft(
    test_id: str,
    field_id: str,
    canonical: str,
    sources: list[str],
) -> dict[str, Any]:
    init_db()
    canonical = str(canonical or "").strip()
    with connect() as conn:
        conn.execute(
            "DELETE FROM deemed_draft WHERE test_id = ? AND field_id = ?",
            (test_id, field_id),
        )
        for src in sources or []:
            src = str(src or "").strip()
            if not src:
                continue
            conn.execute(
                """
                INSERT INTO deemed_draft(test_id, field_id, canonical, source_answer)
                VALUES (?, ?, ?, ?)
                """,
                (test_id, field_id, canonical, src),
            )
        conn.commit()
    return get_deemed_draft(test_id, field_id)


def apply_deemed_scoring_to_field(
    test_id: str,
    field_id: str,
    canonical: str,
    sources: list[str],
) -> dict[str, Any]:
    canonical = str(canonical or "").strip()
    if not canonical:
        raise ValueError("正答例を入力してください。")

    source_set = {
        str(s).strip()
        for s in (sources or [])
        if str(s).strip() and str(s).strip() != canonical
    }
    if not source_set:
        raise ValueError("みなし対象の解答を1件以上選択してください。")

    save_deemed_scoring_draft(test_id, field_id, canonical, list(source_set))

    updated_count = rewrite_field_texts(
        test_id,
        field_id,
        lambda answer: answer in source_set,
        canonical,
    )

    now = _now()
    with connect() as conn:
        for src in source_set:
            conn.execute(
                """
                INSERT INTO deemed_scoring(
                    test_id, field_id, canonical, source_answer, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (test_id, field_id, canonical, src, now),
            )
        conn.execute(
            "DELETE FROM deemed_draft WHERE test_id = ? AND field_id = ?",
            (test_id, field_id),
        )
        conn.commit()

    return {
        "answers": get_unique_answers(test_id, field_id),
        "updatedCount": updated_count,
        "canonical": canonical,
    }
