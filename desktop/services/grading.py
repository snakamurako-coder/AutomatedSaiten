"""採点実行（⑤）と考査総括。"""

from __future__ import annotations

import json
from typing import Any

from models.criteria_repo import build_rule_map
from models.database import connect, init_db
from models.test_repo import (
    get_all_results,
    get_answer_fields,
    get_points_conn,
    touch_progress,
)


def execute_grading(test_id: str) -> dict[str, Any]:
    init_db()
    results = get_all_results(test_id)
    if not results:
        raise ValueError("採点対象データがありません。")

    fields = get_answer_fields(test_id)
    rule_map = build_rule_map(test_id)
    unregistered_count = 0

    with connect() as conn:
        for row in results:
            judgments: dict[str, str] = {}
            scores: dict[str, int] = {}
            for f in fields:
                fid = f["id"]
                answer = str(row.get("textMapping", {}).get(fid, "") or "").strip() or "なし"
                rule = rule_map.get(fid, {}).get(answer)
                if rule:
                    judgments[fid] = rule["judgment"]
                    scores[fid] = int(rule["score"])
                else:
                    judgments[fid] = "×"
                    scores[fid] = 0
                    unregistered_count += 1

            conn.execute(
                """
                UPDATE results
                SET judgments_json = ?, scores_json = ?
                WHERE id = ? AND test_id = ?
                """,
                (
                    json.dumps(judgments, ensure_ascii=False),
                    json.dumps(scores, ensure_ascii=False),
                    row["id"],
                    test_id,
                ),
            )
        conn.commit()

    touch_progress(test_id, 5, "採点完了")
    build_summary(test_id, unregistered_count)
    return {"gradedCount": len(results), "unregisteredCount": unregistered_count}


def build_summary(test_id: str, unregistered_count: int = 0) -> int:
    init_db()
    results = get_all_results(test_id)
    fields = get_answer_fields(test_id)

    with connect() as conn:
        points = get_points_conn(conn, test_id)
        conn.execute("DELETE FROM summary_rows WHERE test_id = ?", (test_id,))

        student_count = len(results)
        rows_to_insert: list[tuple[str, str, str, str, str]] = [
            (test_id, "全体", "受験者数", str(student_count), ""),
            (
                test_id,
                "全体",
                "未登録パターン照合数",
                str(unregistered_count),
                "採点基準に無い解答",
            ),
        ]

        for f in fields:
            label = f.get("displayName") or f["id"]
            fid = f["id"]
            counts = {"○": 0, "△": 0, "×": 0, "other": 0}
            total_score = 0
            for row in results:
                j = str(row.get("judgments", {}).get(fid, "") or "")
                if j in counts:
                    counts[j] += 1
                else:
                    counts["other"] += 1
                total_score += int(row.get("scores", {}).get(fid, 0) or 0)

            denom = student_count or 1
            max_pts = points.get(fid, 0)
            rows_to_insert.extend(
                [
                    (test_id, "設問", f"{label}_○人数", str(counts["○"]), ""),
                    (test_id, "設問", f"{label}_△人数", str(counts["△"]), ""),
                    (test_id, "設問", f"{label}_×人数", str(counts["×"]), ""),
                    (
                        test_id,
                        "設問",
                        f"{label}_○率",
                        f"{round(counts['○'] / denom * 1000) / 10}%",
                        "",
                    ),
                    (
                        test_id,
                        "設問",
                        f"{label}_△率",
                        f"{round(counts['△'] / denom * 1000) / 10}%",
                        "",
                    ),
                    (
                        test_id,
                        "設問",
                        f"{label}_×率",
                        f"{round(counts['×'] / denom * 1000) / 10}%",
                        "",
                    ),
                    (
                        test_id,
                        "設問",
                        f"{label}_平均点",
                        str(round(total_score / denom * 100) / 100 if student_count else 0),
                        f"満点={max_pts}",
                    ),
                ]
            )

        conn.executemany(
            """
            INSERT INTO summary_rows(test_id, category, item, value, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        conn.commit()
    return len(rows_to_insert)


def get_summary_data(test_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT category, item, value, note
            FROM summary_rows WHERE test_id = ?
            ORDER BY id
            """,
            (test_id,),
        ).fetchall()
    return [
        {
            "category": r["category"],
            "item": r["item"],
            "value": r["value"],
            "note": r["note"] or "",
        }
        for r in rows
    ]
