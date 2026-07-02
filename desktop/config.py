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
        return {
            "vision_api_key": "",
            "ocr_engine": "tesseract",
            "default_orientation": "landscape",
            "tesseract_cmd": "",
            "gemini_api_key": "",
        }
    with path.open(encoding="utf-8") as f:
        return json.load(f)


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
