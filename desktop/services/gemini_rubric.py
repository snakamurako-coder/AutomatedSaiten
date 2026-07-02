"""Gemini API による採点基準の AI 原案生成。"""

from __future__ import annotations

import json
from typing import Any

import requests

from config import load_config
from models.test_repo import get_points_conn
from models.database import connect, init_db


def generate_rubric_with_gemini(
    test_id: str,
    field_id: str,
    unique_answers: list[dict[str, Any]],
) -> dict[str, Any]:
    init_db()
    cfg = load_config()
    api_key = (cfg.get("gemini_api_key") or "").strip()
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY が未設定です。メニューの「詳細設定」から Gemini API キーを登録してください。"
        )

    with connect() as conn:
        points = get_points_conn(conn, test_id)
    max_score = points.get(field_id, 5)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    prompt = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "あなたは厳格かつ公平なテスト採点基準を策定する専門家です。"
                        "各解答に対し、○（満点）、△（部分点）、×（0点）の判定と"
                        "付与得点（0〜満点の整数）および根拠をJSONで返してください。"
                        "解答が「なし」の場合は×・0点としてください。"
                    )
                }
            ]
        },
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            f"記述欄ID: {field_id}, 満点: {max_score}点。"
                            f"ユニーク解答リスト:\n{json.dumps(unique_answers, ensure_ascii=False)}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "scrutinized_list": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "answer_text": {"type": "STRING"},
                                "judgment": {"type": "STRING"},
                                "recommended_score": {"type": "INTEGER"},
                                "reason": {"type": "STRING"},
                            },
                            "required": [
                                "answer_text",
                                "judgment",
                                "recommended_score",
                                "reason",
                            ],
                        },
                    }
                },
                "required": ["scrutinized_list"],
            },
        },
    }

    resp = requests.post(url, json=prompt, timeout=120)
    body = resp.json()
    if "error" in body:
        raise ValueError(f"Gemini API: {body['error']}")

    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def test_gemini_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Gemini API キーが空です。")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={key}"
    )
    payload = {
        "contents": [{"parts": [{"text": "接続確認。1文字でよいので応答してください。"}]}],
        "generationConfig": {"maxOutputTokens": 8},
    }
    resp = requests.post(url, json=payload, timeout=30)
    body = resp.json()
    if "error" in body:
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ValueError(msg or str(err))
    if not body.get("candidates"):
        raise ValueError("Gemini API 応答が空です。")
    return "Gemini API に接続できました。"
