"""OCR（Google Vision API / OpenAI API）。"""

from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np
from config import load_config
from services.google_http import post_json
from services.image_warp import crop_region
from services.openai_http import post_openai_json

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

OCR_ENGINE_VISION = "vision"
OCR_ENGINE_OPENAI = "openai"
DEFAULT_OPENAI_OCR_MODEL = "gpt-4o-mini"


def normalize_ocr_lang(lang: str | None) -> str:
    return "ja" if str(lang or "").lower() == "ja" else "en"


def normalize_ocr_engine(engine: str | None) -> str:
    """設定上の OCR エンジン名を正規化する（旧 tesseract は openai へ移行）。"""
    value = (engine or OCR_ENGINE_OPENAI).strip().lower()
    if value == "tesseract":
        return OCR_ENGINE_OPENAI
    if value in (OCR_ENGINE_VISION, OCR_ENGINE_OPENAI):
        return value
    return OCR_ENGINE_OPENAI


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


def _openai_ocr_prompt(ocr_lang: str) -> str:
    if normalize_ocr_lang(ocr_lang) == "ja":
        return (
            "この画像に写っている手書きまたは活字の文字をすべて抽出してください。"
            "説明・補足・引用符は付けず、読み取った文字列だけを返してください。"
            "文字が無い場合は「なし」とだけ返してください。"
        )
    return (
        "Extract all handwritten or printed text in this image. "
        "Return only the transcribed text without explanation or quotes. "
        'If there is no text, return exactly "なし".'
    )


def _extract_openai_message_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("OpenAI API 応答に choices がありません。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        text = "".join(parts).strip()
    else:
        text = str(content or "").strip()
    return text or "なし"


def call_openai_ocr(image_bgr: np.ndarray, ocr_lang: str, *, api_key: str | None = None) -> str:
    cfg = load_config()
    key = (api_key if api_key is not None else cfg.get("openai_api_key") or "").strip()
    if not key:
        raise ValueError(
            "OpenAI API キーが未設定です。メニューの「詳細設定」から OpenAI API キーを登録してください。"
        )

    model = (cfg.get("openai_ocr_model") or DEFAULT_OPENAI_OCR_MODEL).strip()
    jpeg_bytes = _image_to_jpeg_bytes(image_bgr)
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _openai_ocr_prompt(ocr_lang)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
    }
    data = post_openai_json(key, payload)
    text = _extract_openai_message_text(data)
    return text or "なし"


def call_vision_api(image_bytes: bytes, language_hints: list[str]) -> dict[str, Any]:
    cfg = load_config()
    api_key = (cfg.get("vision_api_key") or "").strip()
    if not api_key:
        raise ValueError(
            "Vision API キーが未設定です。メニューの「詳細設定」から Vision API キーを登録してください。"
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
    data = post_json(url, payload)
    if not data.get("responses"):
        raise ValueError("Vision API 応答が空です。")
    return data["responses"][0]


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


def _run_openai_ocr_on_fields(
    warped_bgr: np.ndarray,
    fields: list[dict[str, Any]],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for f in fields:
        crop = crop_region(warped_bgr, f["x"], f["y"], f["width"], f["height"])
        mapping[f["id"]] = call_openai_ocr(crop, f.get("ocrLang", "en"))
    return mapping


def run_ocr_on_warped_image(
    warped_bgr: np.ndarray,
    fields: list[dict[str, Any]],
) -> dict[str, str]:
    if not fields:
        raise ValueError("記述欄が設定されていません。")

    cfg = load_config()
    engine = normalize_ocr_engine(cfg.get("ocr_engine"))

    if engine == OCR_ENGINE_OPENAI:
        return _run_openai_ocr_on_fields(warped_bgr, fields)

    if fields_need_per_crop_ocr(fields):
        mapping: dict[str, str] = {}
        for f in fields:
            crop = crop_region(warped_bgr, f["x"], f["y"], f["width"], f["height"])
            result = call_vision_api(
                _image_to_jpeg_bytes(crop),
                ocr_lang_to_hints(f.get("ocrLang")),
            )
            mapping[f["id"]] = extract_text_from_single_crop(result)
        return mapping

    unified_lang = fields[0].get("ocrLang", "en")
    vision_result = call_vision_api(
        _image_to_jpeg_bytes(warped_bgr),
        ocr_lang_to_hints(unified_lang),
    )
    boxes = [
        {"id": f["id"], "x": f["x"], "y": f["y"], "w": f["width"], "h": f["height"]}
        for f in fields
    ]
    mapping = extract_text_from_boxes(vision_result, boxes)

    for f in fields:
        mapping.setdefault(f["id"], "なし")
    return mapping


def check_ocr_config() -> dict[str, Any]:
    cfg = load_config()
    engine = normalize_ocr_engine(cfg.get("ocr_engine"))
    if engine == OCR_ENGINE_VISION:
        key = (cfg.get("vision_api_key") or "").strip()
        if key:
            return {"configured": True, "engine": OCR_ENGINE_VISION, "message": "Vision API キーが設定されています。"}
        return {
            "configured": False,
            "engine": OCR_ENGINE_VISION,
            "message": "Vision API キーが未設定です。「詳細設定」で Vision API キーを登録してください。",
        }
    key = (cfg.get("openai_api_key") or "").strip()
    model = (cfg.get("openai_ocr_model") or DEFAULT_OPENAI_OCR_MODEL).strip()
    if key:
        return {
            "configured": True,
            "engine": OCR_ENGINE_OPENAI,
            "message": f"OpenAI API（{model}）で OCR します。",
        }
    return {
        "configured": False,
        "engine": OCR_ENGINE_OPENAI,
        "message": "OpenAI API キーが未設定です。「詳細設定」で OpenAI API キーを登録してください。",
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
    data = post_json(url, payload)
    if not data.get("responses"):
        raise ValueError("Vision API 応答が空です。")
    return "Vision API に接続できました。"


def test_openai_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("OpenAI API キーが空です。")
    cfg = load_config()
    model = (cfg.get("openai_ocr_model") or DEFAULT_OPENAI_OCR_MODEL).strip()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "max_tokens": 8,
    }
    data = post_openai_json(key, payload)
    _extract_openai_message_text(data)
    return f"OpenAI API に接続できました（モデル: {model}）。"
