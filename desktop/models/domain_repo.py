"""⑥ 領域設定と領域得点・総計点の計算。"""

from __future__ import annotations

import json
from typing import Any

from models.database import connect, init_db
from models.test_repo import (
    get_answer_fields,
    get_points_conn,
    touch_progress_conn,
)

# 領域の3分類（GAS: 大問 / 範囲 / 能力）
DOMAIN_KINDS = [("daiMon", "大問"), ("hanI", "範囲"), ("noryoku", "能力")]


def get_domain_settings(test_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT field_id, dai_mon, han_i, noryoku FROM domain_settings WHERE test_id = ?",
            (test_id,),
        ).fetchall()
    return [
        {
            "fieldId": r["field_id"],
            "daiMon": r["dai_mon"] or "",
            "hanI": r["han_i"] or "",
            "noryoku": r["noryoku"] or "",
        }
        for r in rows
    ]


def get_domain_settings_for_ui(test_id: str) -> list[dict[str, Any]]:
    """記述欄一覧と領域設定をマージした UI 用行を返す。"""
    fields = get_answer_fields(test_id)
    settings = {s["fieldId"]: s for s in get_domain_settings(test_id)}
    out = []
    for f in fields:
        s = settings.get(f["id"], {})
        out.append(
            {
                "fieldId": f["id"],
                "displayName": f["displayName"] or f["id"],
                "daiMon": s.get("daiMon", ""),
                "hanI": s.get("hanI", ""),
                "noryoku": s.get("noryoku", ""),
            }
        )
    return out


def save_domain_settings(test_id: str, settings: list[dict[str, Any]]) -> int:
    fields = get_answer_fields(test_id)
    if fields and not settings:
        raise ValueError("領域設定が空のため保存しません（既存データを保護しています）。")
    with connect() as conn:
        conn.execute("DELETE FROM domain_settings WHERE test_id = ?", (test_id,))
        for s in settings:
            conn.execute(
                """
                INSERT INTO domain_settings(test_id, field_id, dai_mon, han_i, noryoku)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    str(s.get("fieldId") or ""),
                    str(s.get("daiMon") or "").strip(),
                    str(s.get("hanI") or "").strip(),
                    str(s.get("noryoku") or "").strip(),
                ),
            )
        touch_progress_conn(conn, test_id, 6)
        conn.commit()
    return len(settings)


def _domain_groups(settings: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """{分類プレフィックス: {ラベル: [fieldId, ...]}} を返す。"""
    groups: dict[str, dict[str, list[str]]] = {}
    for attr, prefix in DOMAIN_KINDS:
        by_label: dict[str, list[str]] = {}
        for s in settings:
            label = str(s.get(attr) or "").strip()
            if not label:
                continue
            by_label.setdefault(label, []).append(s["fieldId"])
        groups[prefix] = by_label
    return groups


def get_domain_column_labels(test_id: str) -> list[str]:
    """領域列名（例: 大問1_得点）をソートして返す。"""
    groups = _domain_groups(get_domain_settings(test_id))
    labels = []
    for _attr, prefix in DOMAIN_KINDS:
        for label in sorted(groups.get(prefix, {}).keys()):
            labels.append(f"{prefix}{label}_得点")
    return labels


def get_domain_max_score(test_id: str, domain_column: str) -> int:
    """領域列名（大問1_得点）の満点（属する記述欄の配点合計）を返す。"""
    settings = get_domain_settings(test_id)
    groups = _domain_groups(settings)
    with connect() as conn:
        points = get_points_conn(conn, test_id)
    for _attr, prefix in DOMAIN_KINDS:
        for label, field_ids in groups.get(prefix, {}).items():
            if f"{prefix}{label}_得点" == domain_column:
                return sum(int(points.get(fid, 0)) for fid in field_ids)
    return 0


def calculate_domain_scores(test_id: str) -> int:
    """全結果行の領域得点・外部得点・総計点を再計算して保存する。

    GAS 版と同じく、総計点 = Σ記述欄得点 + 外部連携得点（領域列は内訳のみ）。
    """
    init_db()
    settings = get_domain_settings(test_id)
    groups = _domain_groups(settings)
    fields = get_answer_fields(test_id)
    field_ids = [f["id"] for f in fields]

    updated = 0
    with connect() as conn:
        # 外部得点マップ（同一 ID は後勝ち）
        ext_rows = conn.execute(
            "SELECT student_id, score FROM external_scores WHERE test_id = ? ORDER BY id",
            (test_id,),
        ).fetchall()
        ext_map = {r["student_id"]: float(r["score"] or 0) for r in ext_rows}

        rows = conn.execute(
            "SELECT id, student_id, scores_json FROM results WHERE test_id = ?",
            (test_id,),
        ).fetchall()
        for row in rows:
            scores = json.loads(row["scores_json"] or "{}")
            domain_scores: dict[str, float] = {}
            for _attr, prefix in DOMAIN_KINDS:
                for label, fids in groups.get(prefix, {}).items():
                    domain_scores[f"{prefix}{label}_得点"] = sum(
                        int(scores.get(fid, 0) or 0) for fid in fids
                    )
            subtotal = sum(int(scores.get(fid, 0) or 0) for fid in field_ids)
            external = ext_map.get(str(row["student_id"] or ""), 0.0)
            total = subtotal + external
            conn.execute(
                """
                UPDATE results
                SET domain_scores_json = ?, external_score = ?, total_score = ?
                WHERE id = ?
                """,
                (
                    json.dumps(domain_scores, ensure_ascii=False),
                    external,
                    total,
                    row["id"],
                ),
            )
            updated += 1
        conn.commit()
    return updated
