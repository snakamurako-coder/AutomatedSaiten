"""OCR（Google Vision API / Tesseract）。"""

from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np
import requests

from config import load_config
from services.image_warp import crop_region

_MIN_TEST_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAB"
    "AAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA"
    "/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAA"
    "AAGPB//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAA"
    "AAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//EABQQAQAAAAAA"
    "AAAAAAAAAAAAAAD/2gAIAQEABj8Cf//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAT8hf//Z"
)


def normalize_ocr_lang(lang: str | None) -> str:
    return "ja" if str(lang or "").lower() == "ja" else "en"


def ocr_lang_to_hints(ocr_lang: str) -> list[str]:
    return ["ja"] if normalize_ocr_lang(ocr_lang) == "ja" else ["en"]


def fields_need_per_crop_ocr(fields: list[dict[str, Any]]) -> bool:
    if len(fields) <= 1:
        return False
    first = normalize_ocr_lang(fields[0].get("ocrLang"))
    return any(normalize_ocr_lang(f.get("ocrLang")) != first for f in fields[1:])


def _image_to_jpeg_bytes(image_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ValueError("JPEG エンコードに失敗しました。")
    return buf.tobytes()


def call_vision_api(image_bytes: bytes, language_hints: list[str]) -> dict[str, Any]:
    cfg = load_config()
    api_key = (cfg.get("vision_api_key") or "").strip()
    if not api_key:
        raise ValueError(
            "VISION_API_KEY が未設定です。メニューの「詳細設定」から Vision API キーを登録してください。"
        )

    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": language_hints or ["ja"]},
            }
        ]
    }
    resp = requests.post(url, json=payload, timeout=60)
    data = resp.json()
    if "error" in data:
        raise ValueError(f"Vision API: {data['error']}")
    if not data.get("responses"):
        raise ValueError("Vision API 応答が空です。")
    return data["responses"][0]


def call_tesseract(image_bgr: np.ndarray, ocr_lang: str) -> str:
    try:
        import pytesseract
    except ImportError as e:
        raise ValueError("pytesseract がインストールされていません。") from e

    cfg = load_config()
    tesseract_cmd = (cfg.get("tesseract_cmd") or "").strip()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    lang = "jpn" if normalize_ocr_lang(ocr_lang) == "ja" else "eng"
    text = pytesseract.image_to_string(image_bgr, lang=lang)
    text = (text or "").strip()
    return text or "なし"


def extract_text_from_single_crop(vision_result: dict[str, Any]) -> str:
    annotations = vision_result.get("textAnnotations") or []
    if not annotations:
        return "なし"
    text = str(annotations[0].get("description") or "").strip()
    return text or "なし"


def extract_text_from_boxes(
    vision_result: dict[str, Any],
    target_boxes: list[dict[str, Any]],
) -> dict[str, str]:
    annotations = vision_result.get("textAnnotations") or []
    mapping: dict[str, str] = {}
    for box in target_boxes:
        text_in_box: list[tuple[str, float, float]] = []
        for anno in annotations[1:]:
            poly = anno.get("boundingPoly") or {}
            vertices = poly.get("vertices") or []
            if len(vertices) < 4:
                continue
            cx = sum(v.get("x", 0) for v in vertices) / 4
            cy = sum(v.get("y", 0) for v in vertices) / 4
            bx, by, bw, bh = box["x"], box["y"], box["w"], box["h"]
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                text_in_box.append((anno.get("description") or "", cx, cy))
        text_in_box.sort(key=lambda t: (round(t[2] / 15), t[1]))
        final = "".join(t[0] for t in text_in_box).strip()
        mapping[str(box["id"])] = final or "なし"
    return mapping


def run_ocr_on_warped_image(
    warped_bgr: np.ndarray,
    fields: list[dict[str, Any]],
) -> dict[str, str]:
    if not fields:
        raise ValueError("記述欄が設定されていません。")

    cfg = load_config()
    engine = (cfg.get("ocr_engine") or "tesseract").lower()

    if fields_need_per_crop_ocr(fields):
        mapping: dict[str, str] = {}
        for f in fields:
            crop = crop_region(warped_bgr, f["x"], f["y"], f["width"], f["height"])
            if engine == "vision":
                result = call_vision_api(
                    _image_to_jpeg_bytes(crop),
                    ocr_lang_to_hints(f.get("ocrLang")),
                )
                mapping[f["id"]] = extract_text_from_single_crop(result)
            else:
                mapping[f["id"]] = call_tesseract(crop, f.get("ocrLang", "en"))
        return mapping

    unified_lang = fields[0].get("ocrLang", "en")
    if engine == "vision":
        vision_result = call_vision_api(
            _image_to_jpeg_bytes(warped_bgr),
            ocr_lang_to_hints(unified_lang),
        )
        boxes = [
            {"id": f["id"], "x": f["x"], "y": f["y"], "w": f["width"], "h": f["height"]}
            for f in fields
        ]
        mapping = extract_text_from_boxes(vision_result, boxes)
    else:
        full_text = call_tesseract(warped_bgr, unified_lang)
        mapping = {f["id"]: full_text for f in fields}

    for f in fields:
        mapping.setdefault(f["id"], "なし")
    return mapping


def check_ocr_config() -> dict[str, Any]:
    cfg = load_config()
    engine = (cfg.get("ocr_engine") or "tesseract").lower()
    if engine == "vision":
        key = (cfg.get("vision_api_key") or "").strip()
        if key:
            return {"configured": True, "engine": "vision", "message": "Vision API キーが設定されています。"}
        return {
            "configured": False,
            "engine": "vision",
            "message": "Vision API キーが未設定です。「詳細設定」で vision_api_key を登録するか、ocr_engine を tesseract に変更してください。",
        }
    return {
        "configured": True,
        "engine": "tesseract",
        "message": "Tesseract OCR を使用します（日本語は jpn 言語データが必要）。",
    }


def test_vision_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Vision API キーが空です。")
    url = f"https://vision.googleapis.com/v1/images:annotate?key={key}"
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(_MIN_TEST_JPEG).decode("ascii")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["ja"]},
            }
        ]
    }
    resp = requests.post(url, json=payload, timeout=30)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ValueError(msg or str(err))
    if not data.get("responses"):
        raise ValueError("Vision API 応答が空です。")
    return "Vision API に接続できました。"
