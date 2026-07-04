"""記述欄ごとの採点完了判定（手動採点・②・⑥で共有）。"""

from __future__ import annotations

from typing import Any

from models.test_repo import get_all_results, get_answer_fields

# 確定判定（これ以外は未完了扱い）
FINAL_JUDGMENTS = frozenset({"○", "△", "×"})
PENDING_JUDGMENT = "?"


def normalize_judgment(value: Any) -> str:
    """判定記号を正規化。○△× / ?（保留）/ 空。"""
    j = str(value or "").strip()
    if j in ("○", "〇", "◯"):
        return "○"
    if j == "△":
        return "△"
    if j in ("×", "x", "X", "✕", "✖"):
        return "×"
    if j in ("?", "？", "保留"):
        return PENDING_JUDGMENT
    return ""


def is_final_judgment(value: Any) -> bool:
    return normalize_judgment(value) in FINAL_JUDGMENTS


def is_pending_judgment(value: Any) -> bool:
    return normalize_judgment(value) == PENDING_JUDGMENT


def field_grading_stats(
    test_id: str,
    field_id: str,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """1記述欄の採点状況。

    完了条件: 採点結果が1件以上あり、全件が ○ / △ / × のいずれか
    （未採点・保留「?」が1件でもあれば未完了）。
    """
    rows = results if results is not None else get_all_results(test_id)
    fid = str(field_id or "")
    total = len(rows)
    final_n = 0
    pending_n = 0
    ungraded_n = 0
    for row in rows:
        j = normalize_judgment((row.get("judgments") or {}).get(fid, ""))
        if j in FINAL_JUDGMENTS:
            final_n += 1
        elif j == PENDING_JUDGMENT:
            pending_n += 1
        else:
            ungraded_n += 1
    complete = total > 0 and final_n == total
    return {
        "fieldId": fid,
        "total": total,
        "final": final_n,
        "pending": pending_n,
        "ungraded": ungraded_n,
        "complete": complete,
        "label": "完了" if complete else "未完",
    }


def field_grading_complete_map(test_id: str) -> dict[str, bool]:
    """field_id → 採点完了か。"""
    fields = get_answer_fields(test_id)
    results = get_all_results(test_id)
    return {
        f["id"]: bool(field_grading_stats(test_id, f["id"], results)["complete"])
        for f in fields
    }
