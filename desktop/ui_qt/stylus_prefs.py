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

FIT_MODE_WIDTH = "width"
FIT_MODE_HEIGHT = "height"
FIT_MODE_CONTAIN = "contain"
FIT_MODES = frozenset({FIT_MODE_WIDTH, FIT_MODE_HEIGHT, FIT_MODE_CONTAIN})

VERTICAL_ALIGN_TOP = "top"
VERTICAL_ALIGN_CENTER = "center"
VERTICAL_ALIGN_BOTTOM = "bottom"
VERTICAL_ALIGNS = frozenset(
    {VERTICAL_ALIGN_TOP, VERTICAL_ALIGN_CENTER, VERTICAL_ALIGN_BOTTOM}
)


def load_stylus_prefs() -> dict:
    cfg = load_config()
    mode = str(cfg.get("stylus_eraser_mode") or ERASER_MODE_PIXEL).strip().lower()
    if mode not in (ERASER_MODE_PIXEL, ERASER_MODE_STROKE):
        mode = ERASER_MODE_PIXEL
    side = str(cfg.get("maximize_write_palm_grabber_side") or PALM_GRABBER_LEFT).strip().lower()
    if side not in PALM_GRABBER_SIDES:
        side = PALM_GRABBER_LEFT
    fit_mode = str(cfg.get("maximize_write_fit_mode") or FIT_MODE_CONTAIN).strip().lower()
    if fit_mode not in FIT_MODES:
        fit_mode = FIT_MODE_CONTAIN
    v_align = str(cfg.get("maximize_write_vertical_align") or VERTICAL_ALIGN_CENTER).strip().lower()
    if v_align not in VERTICAL_ALIGNS:
        v_align = VERTICAL_ALIGN_CENTER
    return {
        "palm_rejection": bool(cfg.get("stylus_palm_rejection", True)),
        "eraser_mode": mode,
        "maximize_write_palm_grabber_side": side,
        "maximize_write_fit_mode": fit_mode,
        "maximize_write_vertical_align": v_align,
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


def save_maximize_write_fit_mode(mode: str) -> None:
    m = str(mode or FIT_MODE_CONTAIN).strip().lower()
    if m not in FIT_MODES:
        m = FIT_MODE_CONTAIN
    cfg = load_config()
    cfg["maximize_write_fit_mode"] = m
    save_config(cfg)


def save_maximize_write_vertical_align(align: str) -> None:
    a = str(align or VERTICAL_ALIGN_CENTER).strip().lower()
    if a not in VERTICAL_ALIGNS:
        a = VERTICAL_ALIGN_CENTER
    cfg = load_config()
    cfg["maximize_write_vertical_align"] = a
    save_config(cfg)
