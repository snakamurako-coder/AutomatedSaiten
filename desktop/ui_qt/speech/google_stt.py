"""Google Speech API（Chromium）— FLAC コマンド不要で PCM を直送する。"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = "http://www.google.com/speech-api/v2/recognize"
DEFAULT_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"


class UnknownValueError(Exception):
    """音声を認識できなかった。"""


class RequestError(Exception):
    """API リクエスト失敗。"""


def recognize_pcm(
    pcm_bytes: bytes,
    *,
    sample_rate: int = 16000,
    language: str = "ja-JP",
    timeout: int = 15,
) -> str:
    """16-bit リニア PCM を Google 音声認識 API に送り、テキストを返す。"""
    if not pcm_bytes:
        raise UnknownValueError()
    params = urlencode(
        {
            "client": "chromium",
            "lang": language,
            "key": DEFAULT_KEY,
            "pFilter": 0,
        }
    )
    url = f"{ENDPOINT}?{params}"
    request = Request(
        url,
        data=pcm_bytes,
        headers={"Content-Type": f"audio/l16; rate={sample_rate}"},
    )
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        raise RequestError(f"recognition request failed: {exc.reason}") from exc
    except URLError as exc:
        raise RequestError(f"recognition connection failed: {exc.reason}") from exc
    return _parse_transcript(response.read().decode("utf-8"))


def _parse_transcript(response_text: str) -> str:
    for line in response_text.split("\n"):
        if not line.strip():
            continue
        payload = json.loads(line)
        results = payload.get("result") or []
        if not results:
            continue
        alternatives = results[0].get("alternative") or []
        if not alternatives:
            raise UnknownValueError()
        transcript = str(alternatives[0].get("transcript") or "").strip()
        if transcript:
            return transcript
        raise UnknownValueError()
    raise UnknownValueError()
