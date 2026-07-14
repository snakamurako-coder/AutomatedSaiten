"""スタイラス関連の設定（config.json）。"""

from __future__ import annotations

from config import load_config, save_config

ERASER_MODE_PIXEL = "pixel"
ERASER_MODE_STROKE = "stroke"

PALM_GRABBER_LEFT = "left"
PALM_GRABBER_CENTER = "center"
PALM_GRABBER_RIGHT = "right"
PALM_GRABBER_SIDES = frozenset(
    {PALM_GRABBER_LEFT, PALM_GRABBER_CENTER, PALM_GRABBER_RIGHT}
)


def load_stylus_prefs() -> dict:
    cfg = load_config()
    mode = str(cfg.get("stylus_eraser_mode") or ERASER_MODE_PIXEL).strip().lower()
    if mode not in (ERASER_MODE_PIXEL, ERASER_MODE_STROKE):
        mode = ERASER_MODE_PIXEL
    side = str(cfg.get("maximize_write_palm_grabber_side") or PALM_GRABBER_LEFT).strip().lower()
    if side not in PALM_GRABBER_SIDES:
        side = PALM_GRABBER_LEFT
    return {
        "palm_rejection": bool(cfg.get("stylus_palm_rejection", True)),
        "eraser_mode": mode,
        "maximize_write_palm_grabber_side": side,
    }


def save_stylus_eraser_mode(mode: str) -> None:
    m = str(mode or ERASER_MODE_PIXEL).strip().lower()
    if m not in (ERASER_MODE_PIXEL, ERASER_MODE_STROKE):
        m = ERASER_MODE_PIXEL
    cfg = load_config()
    cfg["stylus_eraser_mode"] = m
    save_config(cfg)


def save_stylus_palm_rejection(enabled: bool) -> None:
    cfg = load_config()
    cfg["stylus_palm_rejection"] = bool(enabled)
    save_config(cfg)


def save_maximize_write_palm_grabber_side(side: str) -> None:
    s = str(side or PALM_GRABBER_LEFT).strip().lower()
    if s not in PALM_GRABBER_SIDES:
        s = PALM_GRABBER_LEFT
    cfg = load_config()
    cfg["maximize_write_palm_grabber_side"] = s
    save_config(cfg)
