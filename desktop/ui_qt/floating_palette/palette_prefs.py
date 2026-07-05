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

TEXT_PALETTE_COLORS_DEFAULT: tuple[str, ...] = PALETTE_COLORS

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
    "last_input_mode": "draw",
    "text_palette_colors": list(TEXT_PALETTE_COLORS_DEFAULT),
}


def _normalize_hex_color(value: Any, fallback: str) -> str:
    s = str(value or "").strip()
    if not s.startswith("#"):
        return fallback
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return fallback
    try:
        int(h, 16)
    except ValueError:
        return fallback
    return f"#{h.lower()}"


def load_text_palette_colors() -> tuple[str, ...]:
    prefs = load_palette_prefs()
    raw = prefs.get("text_palette_colors")
    if not isinstance(raw, list) or len(raw) != 6:
        return TEXT_PALETTE_COLORS_DEFAULT
    return tuple(
        _normalize_hex_color(raw[i], TEXT_PALETTE_COLORS_DEFAULT[i]) for i in range(6)
    )


def save_text_palette_colors(colors: list[str] | tuple[str, ...]) -> None:
    if len(colors) != 6:
        return
    normalized = [
        _normalize_hex_color(colors[i], TEXT_PALETTE_COLORS_DEFAULT[i]) for i in range(6)
    ]
    save_palette_prefs({"text_palette_colors": normalized})


def reset_text_palette_colors() -> tuple[str, ...]:
    """テンプレート文字色6色をデフォルトに戻す。"""
    colors = list(TEXT_PALETTE_COLORS_DEFAULT)
    save_palette_prefs({"text_palette_colors": colors})
    return TEXT_PALETTE_COLORS_DEFAULT


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
    lim = str(out.get("last_input_mode") or "draw")
    out["last_input_mode"] = lim if lim in ("draw", "text") else "draw"
    return out


def save_palette_prefs(prefs: dict[str, Any]) -> None:
    cfg = load_config()
    merged = dict(_DEFAULTS)
    merged.update(cfg.get("floating_palette") or {})
    merged.update(prefs)
    cfg["floating_palette"] = merged
    save_config(cfg)
