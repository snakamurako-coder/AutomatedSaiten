"""フローティングパレット設定（config.json）。"""

from __future__ import annotations

from typing import Any

from config import load_config, save_config
from models.text_annotation_repo import DEFAULT_TEXT_STYLE, resolve_text_style

VIEW_SIMPLE = "simple"
VIEW_DETAILED = "detailed"

TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_PHRASE = "phrase"
TOOL_NONE = "none"

PALETTE_COLORS = ("#111827", "#dc2626", "#2563eb", "#16a34a", "#ea580c", "#9333ea")

TEXT_PALETTE_COLORS_DEFAULT: tuple[str, ...] = PALETTE_COLORS

# テキストボックス配置時の内蔵既定（詳細設定で上書き）
TEXT_BOX_DEFAULT_STYLE_BUILTIN: dict[str, Any] = dict(DEFAULT_TEXT_STYLE)

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
    "text_box_default_style": dict(TEXT_BOX_DEFAULT_STYLE_BUILTIN),
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


def _normalize_align_h(value: Any) -> str:
    h = str(value or "left").lower()
    return h if h in ("left", "center", "right") else "left"


def _normalize_align_v(value: Any) -> str:
    v = str(value or "top").lower()
    return v if v in ("top", "center", "bottom") else "top"


def _normalize_template_id(value: Any) -> str:
    tid = str(value or "A").upper()
    return tid if tid in ("A", "B") else "A"


def normalize_text_box_default_style(raw: Any) -> dict[str, Any]:
    """配置時デフォルト書式を検証して確定する。"""
    base = dict(TEXT_BOX_DEFAULT_STYLE_BUILTIN)
    if isinstance(raw, dict):
        base.update(raw)
    try:
        font_size = int(round(float(base.get("fontSize") or 14)))
    except (TypeError, ValueError):
        font_size = 14
    try:
        line_spacing = int(round(float(base.get("lineSpacing") or 20)))
    except (TypeError, ValueError):
        line_spacing = 20
    base["fontSize"] = max(6, min(72, font_size))
    base["lineSpacing"] = max(6, min(144, line_spacing))
    base["textColor"] = _normalize_hex_color(
        base.get("textColor"), str(TEXT_BOX_DEFAULT_STYLE_BUILTIN["textColor"])
    )
    base["textAlignH"] = _normalize_align_h(base.get("textAlignH"))
    base["textAlignV"] = _normalize_align_v(base.get("textAlignV"))
    base["templateId"] = _normalize_template_id(base.get("templateId"))
    return resolve_text_style(base)


def load_text_box_default_style() -> dict[str, Any]:
    prefs = load_palette_prefs()
    return normalize_text_box_default_style(prefs.get("text_box_default_style"))


def save_text_box_default_style(style: dict[str, Any] | None) -> None:
    save_palette_prefs(
        {"text_box_default_style": normalize_text_box_default_style(style)}
    )


def reset_text_box_default_style() -> dict[str, Any]:
    style = normalize_text_box_default_style(TEXT_BOX_DEFAULT_STYLE_BUILTIN)
    save_text_box_default_style(style)
    return style


def load_palette_prefs() -> dict[str, Any]:
    cfg = load_config()
    raw = cfg.get("floating_palette") or {}
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        out.update(raw)
    vm = str(out.get("view_mode") or VIEW_SIMPLE)
    out["view_mode"] = vm if vm in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
    lt = str(out.get("last_tool") or TOOL_NONE)
    out["last_tool"] = lt if lt in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_PHRASE, TOOL_NONE) else TOOL_NONE
    lim = str(out.get("last_input_mode") or "draw")
    out["last_input_mode"] = lim if lim in ("draw", "text", "phrase") else "draw"
    out["text_box_default_style"] = normalize_text_box_default_style(
        out.get("text_box_default_style")
    )
    return out


def save_palette_prefs(prefs: dict[str, Any]) -> None:
    cfg = load_config()
    merged = dict(_DEFAULTS)
    merged.update(cfg.get("floating_palette") or {})
    merged.update(prefs)
    if "text_box_default_style" in merged:
        merged["text_box_default_style"] = normalize_text_box_default_style(
            merged.get("text_box_default_style")
        )
    cfg["floating_palette"] = merged
    save_config(cfg)
