"""手動採点 UI の設定読み書き。"""

from __future__ import annotations

from config import load_config, save_config

_DEFAULT_DISPLAY = {
    "showId": True,
    "showFileName": True,
    "showOcrText": True,
    "printMarkMode": False,
}


def manual_grading_hover_toolbar_enabled(cfg: dict | None = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return bool(c.get("manual_grading_hover_toolbar"))


def save_manual_grading_hover_toolbar(enabled: bool) -> None:
    cfg = load_config()
    cfg["manual_grading_hover_toolbar"] = bool(enabled)
    save_config(cfg)


def load_manual_grading_display_prefs(cfg: dict | None = None) -> dict[str, bool]:
    c = cfg if cfg is not None else load_config()
    raw = c.get("manual_grading_display")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_DISPLAY)
    out = dict(_DEFAULT_DISPLAY)
    for key in _DEFAULT_DISPLAY:
        if key in raw:
            out[key] = bool(raw[key])
    return out


def save_manual_grading_display_prefs(prefs: dict[str, bool]) -> None:
    cfg = load_config()
    merged = load_manual_grading_display_prefs(cfg)
    for key in _DEFAULT_DISPLAY:
        if key in prefs:
            merged[key] = bool(prefs[key])
    cfg["manual_grading_display"] = merged
    save_config(cfg)


def _field_zoom_key(test_id: str, field_id: str) -> str:
    return f"{test_id}:{field_id}"


def load_field_zoom_pct(
    test_id: str,
    field_id: str,
    default: int = 100,
    cfg: dict | None = None,
) -> int:
    c = cfg if cfg is not None else load_config()
    raw = c.get("manual_grading_field_zoom_pct")
    if not isinstance(raw, dict):
        return default
    val = raw.get(_field_zoom_key(test_id, field_id))
    if val is None:
        return default
    try:
        return max(30, min(400, int(val)))
    except (TypeError, ValueError):
        return default


def save_field_zoom_pct(test_id: str, field_id: str, pct: int) -> None:
    cfg = load_config()
    raw = cfg.get("manual_grading_field_zoom_pct")
    if not isinstance(raw, dict):
        raw = {}
    raw[_field_zoom_key(test_id, field_id)] = max(30, min(400, int(pct)))
    cfg["manual_grading_field_zoom_pct"] = raw
    save_config(cfg)


_DEFAULT_FIELD_VIEW = {
    "showAllPages": False,
    "pageSize": 20,
    "parallelPaletteMode": False,
}


def _field_view_key(test_id: str, field_id: str) -> str:
    return f"{test_id}:{field_id}"


def load_field_view_prefs(
    test_id: str,
    field_id: str,
    cfg: dict | None = None,
) -> dict[str, bool | int]:
    c = cfg if cfg is not None else load_config()
    raw = c.get("manual_grading_field_view_prefs")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_FIELD_VIEW)
    entry = raw.get(_field_view_key(test_id, field_id))
    if not isinstance(entry, dict):
        return dict(_DEFAULT_FIELD_VIEW)
    out = dict(_DEFAULT_FIELD_VIEW)
    if "showAllPages" in entry:
        out["showAllPages"] = bool(entry["showAllPages"])
    if "parallelPaletteMode" in entry:
        out["parallelPaletteMode"] = bool(entry["parallelPaletteMode"])
    if "pageSize" in entry:
        try:
            out["pageSize"] = max(1, min(500, int(entry["pageSize"])))
        except (TypeError, ValueError):
            pass
    return out


def save_field_view_prefs(
    test_id: str,
    field_id: str,
    *,
    show_all_pages: bool,
    page_size: int,
    parallel_palette_mode: bool,
) -> None:
    cfg = load_config()
    raw = cfg.get("manual_grading_field_view_prefs")
    if not isinstance(raw, dict):
        raw = {}
    raw[_field_view_key(test_id, field_id)] = {
        "showAllPages": bool(show_all_pages),
        "pageSize": max(1, min(500, int(page_size))),
        "parallelPaletteMode": bool(parallel_palette_mode),
    }
    cfg["manual_grading_field_view_prefs"] = raw
    save_config(cfg)
