"""OpenAI API 向け HTTP クライアント（タイムアウト・レート制御・エラー整形）。"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from services.google_http import _CONNECT_TIMEOUT, _READ_TIMEOUT, _session

# 全 OpenAI 呼び出しを直列化し、短時間に詰め込みすぎない
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

_DEFAULT_MIN_INTERVAL_MS = 700
_DEFAULT_MAX_RETRIES = 6


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
    elif (
        status_code == 429
        or "rate limit" in lower
        or "rate_limit" in lower
        or "too many requests" in lower
    ):
        msg += (
            "\n\n【レート制限】\n"
            "短時間にリクエストが多すぎます。"
            "間隔を空けて自動リトライします。"
            "頻発する場合は詳細設定の送信間隔を長くしてください。"
        )
    elif "model" in lower and ("not found" in lower or "does not exist" in lower):
        msg += "\n\n指定モデルが利用できません。config.json の openai_ocr_model を確認してください。"
    return msg


def _config_int(key: str, default: int, *, minimum: int = 0) -> int:
    try:
        from config import load_config

        raw = load_config().get(key, default)
        return max(minimum, int(raw))
    except Exception:  # noqa: BLE001
        return default


def _min_interval_sec() -> float:
    return _config_int("openai_ocr_min_interval_ms", _DEFAULT_MIN_INTERVAL_MS) / 1000.0


def _max_retries() -> int:
    return _config_int("openai_ocr_max_retries", _DEFAULT_MAX_RETRIES, minimum=0)


def _wait_for_rate_slot() -> None:
    """前回呼び出しから最低間隔を空ける（ロック保持中に sleep）。"""
    global _LAST_REQUEST_AT
    interval = _min_interval_sec()
    now = time.monotonic()
    wait = _LAST_REQUEST_AT + interval - now
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_AT = time.monotonic()


def _retry_after_seconds(resp: requests.Response, attempt: int) -> float:
    """Retry-After ヘッダ、なければ指数バックオフ。"""
    header = (resp.headers.get("Retry-After") or "").strip()
    if header:
        try:
            return max(1.0, float(header))
        except ValueError:
            pass
    # 1, 2, 4, 8, 16, 32...（上限 60 秒）
    return min(60.0, float(2 ** attempt))


def _is_rate_limited(resp: requests.Response, data: dict[str, Any]) -> bool:
    if resp.status_code == 429:
        return True
    err = data.get("error")
    if not isinstance(err, dict):
        return False
    code = str(err.get("code") or "").lower()
    etype = str(err.get("type") or "").lower()
    msg = str(err.get("message") or "").lower()
    return (
        "rate_limit" in code
        or "rate_limit" in etype
        or "rate limit" in msg
        or "too many requests" in msg
    )


def post_openai_json(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("OpenAI API キーが空です。")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = "https://api.openai.com/v1/chat/completions"
    retries = _max_retries()

    with _RATE_LOCK:
        for attempt in range(retries + 1):
            _wait_for_rate_slot()
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

            if _is_rate_limited(resp, data):
                if attempt >= retries:
                    raise ValueError(format_openai_api_error(data, resp.status_code))
                delay = _retry_after_seconds(resp, attempt)
                time.sleep(delay)
                # リトライ直後に連射しないよう、最終送信時刻を進める
                global _LAST_REQUEST_AT
                _LAST_REQUEST_AT = time.monotonic()
                continue

            if resp.status_code >= 400 or data.get("error"):
                raise ValueError(format_openai_api_error(data, resp.status_code))
            return data

    raise ValueError("OpenAI API のレート制限を回避できませんでした。")
