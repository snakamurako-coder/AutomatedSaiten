"""⑩ Excel成績出力の詳細設定（config.json）。"""

from __future__ import annotations

from config import load_config, save_config

DEFAULT_HIST_BIN_PCT = 10
DEFAULT_RANK_OVERALL = 48
DEFAULT_RANK_CLASS = 16


def load_excel_export_prefs() -> dict:
    cfg = load_config()
    try:
        hist = int(cfg.get("excel_export_hist_bin_pct") or DEFAULT_HIST_BIN_PCT)
    except (TypeError, ValueError):
        hist = DEFAULT_HIST_BIN_PCT
    hist = max(1, min(50, hist))
    try:
        overall = int(
            cfg.get("excel_export_rank_overall_limit") or DEFAULT_RANK_OVERALL
        )
    except (TypeError, ValueError):
        overall = DEFAULT_RANK_OVERALL
    overall = max(1, min(500, overall))
    try:
        class_lim = int(
            cfg.get("excel_export_rank_class_limit") or DEFAULT_RANK_CLASS
        )
    except (TypeError, ValueError):
        class_lim = DEFAULT_RANK_CLASS
    class_lim = max(1, min(200, class_lim))
    return {
        "hist_bin_pct": hist,
        "rank_overall_limit": overall,
        "rank_class_limit": class_lim,
    }


def save_excel_export_prefs(
    *,
    hist_bin_pct: int | None = None,
    rank_overall_limit: int | None = None,
    rank_class_limit: int | None = None,
) -> dict:
    prefs = load_excel_export_prefs()
    if hist_bin_pct is not None:
        prefs["hist_bin_pct"] = max(1, min(50, int(hist_bin_pct)))
    if rank_overall_limit is not None:
        prefs["rank_overall_limit"] = max(1, min(500, int(rank_overall_limit)))
    if rank_class_limit is not None:
        prefs["rank_class_limit"] = max(1, min(200, int(rank_class_limit)))
    cfg = load_config()
    cfg["excel_export_hist_bin_pct"] = prefs["hist_bin_pct"]
    cfg["excel_export_rank_overall_limit"] = prefs["rank_overall_limit"]
    cfg["excel_export_rank_class_limit"] = prefs["rank_class_limit"]
    save_config(cfg)
    return prefs
