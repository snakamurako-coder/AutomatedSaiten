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
    # 最大化書き込み: パーム領域グラバーの水平位置
    "maximize_write_palm_grabber_side": "left",
    "faint_check_enabled": True,
    "faint_min_sigma": 12.0,
    "faint_min_p95_p5": 35.0,
    "faint_min_bg_delta": 18.0,
    # 目視・強調ダイアログのユーザー定義プリセット
    # [{name, contrast, clahe, bg_whiten}]  ※値はスライダー整数
    "enhance_presets": [],
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


# 内蔵プリセット（スライダー整数: contrast 100–220, clahe 0–80, bg_whiten 0–100）
BUILTIN_ENHANCE_PRESETS: list[dict] = [
    {"name": "薄い字", "contrast": 145, "clahe": 30, "bg_whiten": 0, "builtin": True},
    {"name": "地色除去", "contrast": 120, "clahe": 15, "bg_whiten": 70, "builtin": True},
    {
        "name": "地色＋薄い字",
        "contrast": 140,
        "clahe": 25,
        "bg_whiten": 55,
        "builtin": True,
    },
]


def _normalize_enhance_preset(raw: dict, *, builtin: bool = False) -> dict | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    try:
        contrast = int(raw.get("contrast", 135))
        clahe = int(raw.get("clahe", 25))
        bg = int(raw.get("bg_whiten", 0))
    except (TypeError, ValueError):
        return None
    return {
        "name": name,
        "contrast": max(100, min(220, contrast)),
        "clahe": max(0, min(80, clahe)),
        "bg_whiten": max(0, min(100, bg)),
        "builtin": builtin,
    }


def list_enhance_presets(cfg: dict | None = None) -> list[dict]:
    """内蔵＋ユーザー定義プリセット（同名はユーザー定義が後勝ちで上書き表示しない＝別名推奨）。"""
    c = cfg if cfg is not None else load_config()
    out = [dict(p) for p in BUILTIN_ENHANCE_PRESETS]
    builtin_names = {p["name"] for p in out}
    for raw in c.get("enhance_presets") or []:
        if not isinstance(raw, dict):
            continue
        p = _normalize_enhance_preset(raw, builtin=False)
        if not p:
            continue
        # 内蔵と同名はスキップ（内蔵を守る）
        if p["name"] in builtin_names:
            continue
        out.append(p)
    return out


def save_enhance_preset(
    name: str,
    *,
    contrast: int,
    clahe: int,
    bg_whiten: int,
) -> list[dict]:
    """ユーザープリセットを追加または同名更新。保存後の全一覧を返す。"""
    preset = _normalize_enhance_preset(
        {
            "name": name,
            "contrast": contrast,
            "clahe": clahe,
            "bg_whiten": bg_whiten,
        },
        builtin=False,
    )
    if not preset:
        raise ValueError("プリセット名が空です。")
    builtin_names = {p["name"] for p in BUILTIN_ENHANCE_PRESETS}
    if preset["name"] in builtin_names:
        raise ValueError(f"「{preset['name']}」は内蔵プリセットのため上書きできません。")
    cfg = load_config()
    rows = [
        _normalize_enhance_preset(r, builtin=False)
        for r in (cfg.get("enhance_presets") or [])
        if isinstance(r, dict)
    ]
    rows = [r for r in rows if r]
    replaced = False
    for i, r in enumerate(rows):
        if r["name"] == preset["name"]:
            rows[i] = {
                "name": preset["name"],
                "contrast": preset["contrast"],
                "clahe": preset["clahe"],
                "bg_whiten": preset["bg_whiten"],
            }
            replaced = True
            break
    if not replaced:
        rows.append(
            {
                "name": preset["name"],
                "contrast": preset["contrast"],
                "clahe": preset["clahe"],
                "bg_whiten": preset["bg_whiten"],
            }
        )
    cfg["enhance_presets"] = rows
    save_config(cfg)
    return list_enhance_presets(cfg)


def delete_enhance_preset(name: str) -> list[dict]:
    """ユーザープリセットを削除。内蔵は削除不可。"""
    name = str(name or "").strip()
    builtin_names = {p["name"] for p in BUILTIN_ENHANCE_PRESETS}
    if name in builtin_names:
        raise ValueError("内蔵プリセットは削除できません。")
    cfg = load_config()
    rows = [
        r
        for r in (cfg.get("enhance_presets") or [])
        if isinstance(r, dict) and str(r.get("name") or "").strip() != name
    ]
    cfg["enhance_presets"] = [
        {
            "name": p["name"],
            "contrast": p["contrast"],
            "clahe": p["clahe"],
            "bg_whiten": p["bg_whiten"],
        }
        for p in (_normalize_enhance_preset(r, builtin=False) for r in rows)
        if p
    ]
    save_config(cfg)
    return list_enhance_presets(cfg)


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
