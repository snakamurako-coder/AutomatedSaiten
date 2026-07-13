"""手書きストローク（スタイラス層）の永続化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from models.database import connect

# 答案全体（補正画像座標）レイヤー。欄またぎ手書き用。
SHEET_FIELD_ID = "__sheet__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_sheet_field_id(field_id: str | None) -> bool:
    return str(field_id or "").strip() == SHEET_FIELD_ID


def get_ink_strokes(test_id: str, result_id: int, field_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT strokes_json FROM ink_strokes "
            "WHERE test_id = ? AND result_id = ? AND field_id = ?",
            (test_id, int(result_id), field_id),
        ).fetchone()
    if not row:
        return []
    try:
        data = json.loads(row["strokes_json"] or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def get_ink_strokes_batch(
    test_id: str, field_id: str, result_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not result_ids:
        return {}
    ids = [int(i) for i in result_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT result_id, strokes_json FROM ink_strokes "
            f"WHERE test_id = ? AND field_id = ? AND result_id IN ({placeholders})",
            (test_id, field_id, *ids),
        ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rid = int(row["result_id"])
        try:
            data = json.loads(row["strokes_json"] or "[]")
            out[rid] = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            out[rid] = []
    return out


def get_ink_strokes_for_result(
    test_id: str, result_id: int
) -> dict[str, list[dict[str, Any]]]:
    """1 答案分の全 field_id → ストロークを1クエリで取得。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT field_id, strokes_json FROM ink_strokes "
            "WHERE test_id = ? AND result_id = ?",
            (test_id, int(result_id)),
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        fid = str(row["field_id"] or "")
        if not fid:
            continue
        try:
            data = json.loads(row["strokes_json"] or "[]")
            out[fid] = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            out[fid] = []
    return out


def save_ink_strokes(
    test_id: str,
    result_id: int,
    field_id: str,
    strokes: list[dict[str, Any]],
) -> None:
    payload = json.dumps(strokes, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO ink_strokes (test_id, result_id, field_id, strokes_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(test_id, result_id, field_id) DO UPDATE SET "
            "strokes_json = excluded.strokes_json, updated_at = excluded.updated_at",
            (test_id, int(result_id), field_id, payload, _now_iso()),
        )
        conn.commit()


def collect_warped_ink_strokes(
    test_id: str,
    result_id: int,
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """記述欄ローカル座標の手書きを補正画像座標へ変換して結合。

    末尾に答案全体レイヤー（__sheet__、すでに warped 座標）を追記する。
    """
    warped: list[dict[str, Any]] = []
    rid = int(result_id)
    by_field = get_ink_strokes_for_result(test_id, rid)
    for f in fields:
        fid = str(f.get("id") or "")
        if not fid or is_sheet_field_id(fid):
            continue
        local = by_field.get(fid) or []
        if not local:
            continue
        ox = float(f.get("x") or 0)
        oy = float(f.get("y") or 0)
        for stroke in local:
            pts = []
            for p in stroke.get("points") or []:
                pts.append(
                    {
                        "x": ox + float(p["x"]),
                        "y": oy + float(p["y"]),
                        "p": float(p.get("p", 1.0)),
                    }
                )
            if not pts:
                continue
            warped.append(
                {
                    "fieldId": fid,
                    "color": stroke.get("color") or "#111827",
                    "alpha": float(stroke.get("alpha", 1.0)),
                    "baseWidth": float(stroke.get("baseWidth") or 2.5),
                    "points": pts,
                }
            )
    # シート層は変換なし（補正画像座標のまま）。欄ローカルより手前に重ねる。
    for stroke in by_field.get(SHEET_FIELD_ID) or []:
        pts = list(stroke.get("points") or [])
        if not pts:
            continue
        warped.append(
            {
                "fieldId": SHEET_FIELD_ID,
                "color": stroke.get("color") or "#111827",
                "alpha": float(stroke.get("alpha", 1.0)),
                "baseWidth": float(stroke.get("baseWidth") or 2.5),
                "points": pts,
            }
        )
    return warped


def project_sheet_ink_to_field_local(
    sheet_strokes: list[dict[str, Any]],
    field: dict[str, Any],
) -> list[dict[str, Any]]:
    """シート（warped）インクを記述欄ローカル座標へ投影し、欄外はクリップする。

    編集用リストには入れず、表示専用レイヤ向け。
    """
    ox = float(field.get("x") or 0)
    oy = float(field.get("y") or 0)
    fw = float(field.get("width") or 0)
    fh = float(field.get("height") or 0)
    if fw <= 0 or fh <= 0:
        return []
    out: list[dict[str, Any]] = []
    for stroke in sheet_strokes or []:
        local_pts: list[dict[str, Any]] = []
        for p in stroke.get("points") or []:
            lx = float(p.get("x") or 0) - ox
            ly = float(p.get("y") or 0) - oy
            if lx < -2 or ly < -2 or lx > fw + 2 or ly > fh + 2:
                # 欄から大きく外れた点はセグメント区切り（連続ストロークを分割）
                if local_pts:
                    out.append(
                        {
                            "fieldId": str(field.get("id") or ""),
                            "color": stroke.get("color") or "#111827",
                            "alpha": float(stroke.get("alpha", 1.0)),
                            "baseWidth": float(stroke.get("baseWidth") or 2.5),
                            "points": local_pts,
                            "source": "sheet",
                        }
                    )
                    local_pts = []
                continue
            local_pts.append(
                {
                    "x": lx,
                    "y": ly,
                    "p": float(p.get("p", 1.0)),
                }
            )
        if local_pts:
            out.append(
                {
                    "fieldId": str(field.get("id") or ""),
                    "color": stroke.get("color") or "#111827",
                    "alpha": float(stroke.get("alpha", 1.0)),
                    "baseWidth": float(stroke.get("baseWidth") or 2.5),
                    "points": local_pts,
                    "source": "sheet",
                }
            )
    return out


def field_local_ink_to_warped(
    local_strokes: list[dict[str, Any]],
    field: dict[str, Any],
) -> list[dict[str, Any]]:
    """欄ローカルインクを補正画像座標へ変換（全容下敷き用）。"""
    ox = float(field.get("x") or 0)
    oy = float(field.get("y") or 0)
    fid = str(field.get("id") or "")
    out: list[dict[str, Any]] = []
    for stroke in local_strokes or []:
        pts = []
        for p in stroke.get("points") or []:
            pts.append(
                {
                    "x": ox + float(p.get("x") or 0),
                    "y": oy + float(p.get("y") or 0),
                    "p": float(p.get("p", 1.0)),
                }
            )
        if not pts:
            continue
        out.append(
            {
                "fieldId": fid,
                "color": stroke.get("color") or "#111827",
                "alpha": float(stroke.get("alpha", 1.0)),
                "baseWidth": float(stroke.get("baseWidth") or 2.5),
                "points": pts,
            }
        )
    return out
