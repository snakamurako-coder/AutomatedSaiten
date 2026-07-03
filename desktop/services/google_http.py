"""Google API 向け HTTP クライアント（タイムアウト・エラー整形）。"""

from __future__ import annotations

import socket
import sys
from typing import Any

import requests

_CONNECT_TIMEOUT = 8
_READ_TIMEOUT = 25
_SESSION: requests.Session | None = None


def _prefer_ipv4_on_windows() -> None:
    """一部 Windows 環境で IPv6 接続がハングするのを避ける。"""
    if sys.platform != "win32":
        return
    try:
        import urllib3.util.connection as urllib3_cn

        urllib3_cn.allowed_gai_family = lambda: socket.AF_INET  # type: ignore[assignment]
    except Exception:
        pass


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _prefer_ipv4_on_windows()
        _SESSION = requests.Session()
        _SESSION.trust_env = False
    return _SESSION


def _hint_for_message(msg: str) -> str:
    lower = msg.lower()
    if "referer" in lower or "referrer" in lower:
        return (
            "\n\n【デスクトップアプリ向けの設定】\n"
            "API キーの「アプリケーションの制限」が「HTTP リファラー（ウェブサイト）」"
            "になっていると、ブラウザ以外（この PC アプリ）からは使えません。\n"
            "Google Cloud Console → 認証情報 → 該当 API キー → "
            "アプリケーションの制限を「なし」（または IP アドレス）に変更してください。"
        )
    if "ip address" in lower or "caller ip" in lower:
        return (
            "\n\n【IP 制限】\n"
            "API キーに IP アドレス制限がある場合、お使いの PC のグローバル IP を"
            "許可リストに追加するか、制限を「なし」にしてください。"
        )
    if "api key not valid" in lower or "invalid api key" in lower:
        return "\n\nキーが誤っているか、別プロジェクトのキーです。"
    if "has not been used" in lower or "is disabled" in lower or "not enabled" in lower:
        return (
            "\n\n【API の有効化】\n"
            "Google Cloud Console → API とサービス → ライブラリ で "
            "「Cloud Vision API」を有効にしてください。"
        )
    if "billing" in lower:
        return "\n\n【課金】\nVision API を使うにはプロジェクトで課金（請求先アカウント）の有効化が必要です。"
    return ""


def format_google_api_error(data: dict[str, Any], status_code: int | None = None) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or err)
    elif err:
        msg = str(err)
    else:
        msg = f"HTTP {status_code}" if status_code else "不明なエラー"
    return msg + _hint_for_message(msg)


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = _session().post(
            url,
            json=payload,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
    except requests.exceptions.ConnectTimeout as e:
        raise ValueError(
            f"Google API に接続できませんでした（{_CONNECT_TIMEOUT} 秒でタイムアウト）。\n"
            "インターネット接続、ファイアウォール、プロキシ設定を確認してください。"
        ) from e
    except requests.exceptions.ReadTimeout as e:
        raise ValueError(
            f"Google API からの応答がありませんでした（{_READ_TIMEOUT} 秒でタイムアウト）。"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ValueError(
            "Google API に接続できませんでした。\n"
            f"詳細: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise ValueError(f"通信エラー: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        snippet = (resp.text or "")[:200]
        raise ValueError(
            f"Google API の応答を解釈できません（HTTP {resp.status_code}）。\n{snippet}"
        ) from e

    if resp.status_code >= 400 or "error" in data:
        raise ValueError(format_google_api_error(data, resp.status_code))
    return data
