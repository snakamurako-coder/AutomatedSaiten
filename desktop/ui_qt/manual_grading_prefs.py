"""手動採点 UI の設定読み書き。"""

from __future__ import annotations

from config import load_config, save_config


def manual_grading_hover_toolbar_enabled(cfg: dict | None = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return bool(c.get("manual_grading_hover_toolbar"))


def save_manual_grading_hover_toolbar(enabled: bool) -> None:
    cfg = load_config()
    cfg["manual_grading_hover_toolbar"] = bool(enabled)
    save_config(cfg)
