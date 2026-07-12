"""アプリ設定・データディレクトリのパス管理。"""

from __future__ import annotations

import json
from pathlib import Path

from constants import FEEDBACK_FOLDER_NAME, ORIGINAL_ARCHIVE_FOLDER_NAME

DESKTOP_ROOT = Path(__file__).resolve().parent
DATA_DIR = DESKTOP_ROOT / "data"
DB_PATH = DATA_DIR / "saiten.db"
CONFIG_PATH = DESKTOP_ROOT / "config.json"
CONFIG_EXAMPLE_PATH = DESKTOP_ROOT / "config.example.json"
IMAGES_ROOT = DATA_DIR / "採点システム画像"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_ROOT.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_data_dirs()
    path = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw or {})
    return merged


DEFAULT_CONFIG: dict = {
    "vision_api_key": "",
    "ocr_engine": "tesseract",
    "default_orientation": "landscape",
    "tesseract_cmd": "",
    "gemini_api_key": "",
    "speech_input_mode": "app",
    "speech_pause_seconds": 1.8,
    "stylus_palm_rejection": True,
    "stylus_eraser_mode": "pixel",
    "faint_check_enabled": True,
    "faint_min_sigma": 12.0,
    "faint_min_p95_p5": 35.0,
    "faint_min_bg_delta": 18.0,
    "floating_palette": {
        "x": 0,
        "y": 0,
        "minimized": False,
        "view_mode": "simple",
        "fab_x": None,
        "fab_y": None,
        "last_color": "#111827",
        "last_width": 2.5,
        "last_alpha": 1.0,
        "last_tool": "pen",
    },
}


def faint_thresholds_from_config(cfg: dict | None = None) -> dict[str, float | bool]:
    """薄い字判定のしきい値（未満で要確認）。"""
    c = cfg if cfg is not None else load_config()
    return {
        "enabled": bool(c.get("faint_check_enabled", True)),
        "min_sigma": float(c.get("faint_min_sigma", DEFAULT_CONFIG["faint_min_sigma"])),
        "min_p95_p5": float(c.get("faint_min_p95_p5", DEFAULT_CONFIG["faint_min_p95_p5"])),
        "min_bg_delta": float(
            c.get("faint_min_bg_delta", DEFAULT_CONFIG["faint_min_bg_delta"])
        ),
    }


def save_config(cfg: dict) -> None:
    ensure_data_dirs()
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def test_dir(test_id: str) -> Path:
    return IMAGES_ROOT / test_id


def test_inbox(test_id: str) -> Path:
    return test_dir(test_id) / "inbox"


def test_warped(test_id: str) -> Path:
    return test_dir(test_id) / "warped"


def test_archive(test_id: str) -> Path:
    return test_dir(test_id) / ORIGINAL_ARCHIVE_FOLDER_NAME


def test_model(test_id: str) -> Path:
    return test_dir(test_id) / "model"


def test_feedback(test_id: str) -> Path:
    return test_dir(test_id) / FEEDBACK_FOLDER_NAME


def test_results_excel_path(test_id: str) -> Path:
    """OCR／採点結果 Excel の既定パス（⑩個票フォルダのすぐ上＝同じテスト配下）。"""
    return test_dir(test_id) / "採点結果.xlsx"
