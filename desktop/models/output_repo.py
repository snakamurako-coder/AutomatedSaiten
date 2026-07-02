"""⑩ 出力欄設定（合計欄）と出力書式設定。"""

from __future__ import annotations

import json
from typing import Any

from models.database import connect, init_db
from models.domain_repo import get_domain_column_labels
from models.test_repo import touch_progress_conn

# 書式はテスト横断の共通設定（GAS ではハブ SS に保存していた）。app_state に JSON 保存。
STYLE_STATE_KEY = "feedback_style"

DEFAULT_FEEDBACK_STYLE: dict[str, Any] = {
    "mark": {
        "insetRatio": 0.05,
        "maru": {
            "strokeColor": "#dc2626",
            "fillOpacity": 0.12,
            "strokeOpacity": 1.0,
            "lineWidthRatio": 0.06,
        },
        "sankaku": {"strokeColor": "#ea580c", "strokeOpacity": 1.0, "lineWidthRatio": 0.06},
        "batsu": {"strokeColor": "#2563eb", "strokeOpacity": 1.0, "lineWidthRatio": 0.08},
        "score": {"color": "#111827", "sizeRatio": 0.35, "opacity": 1.0, "fontWeight": "bold"},
    },
    "total": {
        "color": "#111827",
        "sizeRatio": 0.5,
        "opacity": 1.0,
        "fontWeight": "bold",
        "minFontSize": 10,
    },
}


# ==================== 出力欄設定 ====================

def get_output_slots(test_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT slot_key, x, y, width, height, extra_json FROM output_slots WHERE test_id = ?",
            (test_id,),
        ).fetchall()
    out = []
    for r in rows:
        extra = json.loads(r["extra_json"] or "{}")
        out.append(
            {
                "slotKey": r["slot_key"],
                "x": r["x"],
                "y": r["y"],
                "width": r["width"],
                "height": r["height"],
                "printMode": extra.get("printMode") or "number",
            }
        )
    return out


def save_output_slots(test_id: str, slots: list[dict[str, Any]]) -> int:
    if not slots:
        raise ValueError("合計欄を 1 つ以上設定してください。")
    for s in slots:
        if not str(s.get("slotKey") or "").strip():
            raise ValueError("slotKey が空の欄があります。")
    with connect() as conn:
        conn.execute("DELETE FROM output_slots WHERE test_id = ?", (test_id,))
        for s in slots:
            conn.execute(
                """
                INSERT OR REPLACE INTO output_slots(test_id, slot_key, x, y, width, height, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    str(s["slotKey"]),
                    int(s.get("x") or 0),
                    int(s.get("y") or 0),
                    int(s.get("width") or 0),
                    int(s.get("height") or 0),
                    json.dumps(
                        {"printMode": s.get("printMode") or "number"}, ensure_ascii=False
                    ),
                ),
            )
        touch_progress_conn(conn, test_id, 10)
        conn.commit()
    return len(slots)


def get_available_output_slot_keys(test_id: str) -> list[str]:
    """出力欄の候補キー（⑥領域ラベル + 総計点 + 外部連携得点）。"""
    keys = [label.removesuffix("_得点") for label in get_domain_column_labels(test_id)]
    keys.append("総計点")
    keys.append("外部連携得点")
    return keys


# ==================== 出力書式設定 ====================

def _flatten(prefix: str, obj: Any, out: dict[str, str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    else:
        out[prefix] = str(obj)


def _set_deep(target: dict, dotted_key: str, value: str) -> None:
    parts = dotted_key.split(".")
    node = target
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            return
        node = node[p]
    leaf = parts[-1]
    if leaf not in node:
        return
    default = node[leaf]
    try:
        if isinstance(default, bool):
            node[leaf] = value.lower() in ("1", "true", "yes")
        elif isinstance(default, (int, float)):
            node[leaf] = type(default)(float(value))
        else:
            node[leaf] = value
    except (TypeError, ValueError):
        pass


def get_feedback_style() -> dict[str, Any]:
    init_db()
    style = json.loads(json.dumps(DEFAULT_FEEDBACK_STYLE))
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (STYLE_STATE_KEY,)
        ).fetchone()
    if row and row["value"]:
        try:
            flat = json.loads(row["value"])
            for k, v in flat.items():
                _set_deep(style, k, str(v))
        except json.JSONDecodeError:
            pass
    # insetRatio クランプ（GAS 互換）
    style["mark"]["insetRatio"] = max(0.0, min(0.45, float(style["mark"]["insetRatio"])))
    return style


def save_feedback_style(style: dict[str, Any]) -> int:
    flat: dict[str, str] = {}
    _flatten("", style, flat)
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (STYLE_STATE_KEY, json.dumps(flat, ensure_ascii=False, sort_keys=True)),
        )
        conn.commit()
    return len(flat)


def reset_feedback_style() -> dict[str, Any]:
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", (STYLE_STATE_KEY,))
        conn.commit()
    return get_feedback_style()
