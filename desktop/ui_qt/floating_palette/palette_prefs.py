"""フローティングパレット設定（config.json）。"""

from __future__ import annotations

from typing import Any

from config import load_config, save_config

VIEW_SIMPLE = "simple"
VIEW_DETAILED = "detailed"

TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_NONE = "none"

PALETTE_COLORS = ("#111827", "#dc2626", "#2563eb", "#16a34a", "#ea580c", "#9333ea")

_DEFAULTS: dict[str, Any] = {
    "x": 0,
    "y": 0,
    "minimized": False,
    "view_mode": VIEW_SIMPLE,
    "fab_x": None,
    "fab_y": None,
    "last_color": "#111827",
    "last_width": 2.5,
    "last_alpha": 1.0,
    "last_tool": TOOL_NONE,
}


def load_palette_prefs() -> dict[str, Any]:
    cfg = load_config()
    raw = cfg.get("floating_palette") or {}
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        out.update(raw)
    vm = str(out.get("view_mode") or VIEW_SIMPLE)
    out["view_mode"] = vm if vm in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
    lt = str(out.get("last_tool") or TOOL_NONE)
    out["last_tool"] = lt if lt in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_NONE) else TOOL_NONE
    return out


def save_palette_prefs(prefs: dict[str, Any]) -> None:
    cfg = load_config()
    merged = dict(_DEFAULTS)
    merged.update(cfg.get("floating_palette") or {})
    merged.update(prefs)
    cfg["floating_palette"] = merged
    save_config(cfg)
