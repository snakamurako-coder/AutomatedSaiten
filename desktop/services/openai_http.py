"""OpenAI API 向け HTTP クライアント（タイムアウト・エラー整形）。"""

from __future__ import annotations

from typing import Any

import requests

from services.google_http import _CONNECT_TIMEOUT, _READ_TIMEOUT, _session


def format_openai_api_error(data: dict[str, Any], status_code: int | None = None) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or err)
        code = err.get("code")
        if code:
            msg = f"{msg} ({code})"
    elif err:
        msg = str(err)
    else:
        msg = f"HTTP {status_code}" if status_code else "不明なエラー"

    lower = msg.lower()
    if "invalid api key" in lower or "incorrect api key" in lower:
        msg += "\n\nAPI キーが誤っているか、無効化されています。"
    elif "insufficient_quota" in lower or "billing" in lower:
        msg += "\n\n【課金】\nOpenAI の利用枠・請求設定を確認してください。"
    elif "model" in lower and ("not found" in lower or "does not exist" in lower):
        msg += "\n\n指定モデルが利用できません。config.json の openai_ocr_model を確認してください。"
    return msg


def post_openai_json(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("OpenAI API キーが空です。")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = "https://api.openai.com/v1/chat/completions"
    try:
        resp = _session().post(
            url,
            json=payload,
            headers=headers,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
    except requests.exceptions.ConnectTimeout as e:
        raise ValueError(
            f"OpenAI API に接続できませんでした（{_CONNECT_TIMEOUT} 秒でタイムアウト）。\n"
            "インターネット接続、ファイアウォール、プロキシ設定を確認してください。"
        ) from e
    except requests.exceptions.ReadTimeout as e:
        raise ValueError(
            f"OpenAI API からの応答がありませんでした（{_READ_TIMEOUT} 秒でタイムアウト）。"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ValueError(f"OpenAI API に接続できませんでした。\n詳細: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ValueError(f"通信エラー: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        snippet = (resp.text or "")[:200]
        raise ValueError(
            f"OpenAI API の応答を解釈できません（HTTP {resp.status_code}）。\n{snippet}"
        ) from e

    if resp.status_code >= 400 or data.get("error"):
        raise ValueError(format_openai_api_error(data, resp.status_code))
    return data
