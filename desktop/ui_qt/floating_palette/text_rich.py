"""テキストボックスのリッチテキスト（HTML）ヘルパー。"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

TEXT_FORMAT_PLAIN = "plain"
TEXT_FORMAT_HTML = "html"


def plain_to_html(text: str, style: dict[str, Any] | None) -> str:
    st = style or {}
    tc = str(st.get("textColor") or "#111827")
    fs = float(st.get("fontSize") or 14)
    ls = float(st.get("lineSpacing") or fs)
    body = html_lib.escape(str(text or "")).replace("\n", "<br>")
    p_styles = [
        "margin-top:0",
        "margin-bottom:0",
        f"color:{tc}",
        f"font-size:{fs}pt",
        f"line-height:{ls}pt",
        "font-family:Meiryo, sans-serif",
    ]
    if str(st.get("fontWeight") or "") == "bold":
        p_styles.append("font-weight:bold")
    if str(st.get("fontStyle") or "") == "italic":
        p_styles.append("font-style:italic")
    if str(st.get("textDecoration") or "") == "underline":
        p_styles.append("text-decoration:underline")
    p_style = "; ".join(p_styles)
    return (
        '<html><head></head><body style="margin:0; padding:0;">'
        f'<p style="{p_style}">{body}</p>'
        "</body></html>"
    )


def sync_box_html_from_style(box: dict[str, Any]) -> None:
    """style 辞書の文字色・サイズ・装飾を textHtml に反映する。"""
    plain = str(box.get("text") or "")
    if not plain.strip():
        box["textHtml"] = ""
        box["textFormat"] = TEXT_FORMAT_PLAIN
        return
    st = box.get("style") or {}
    box["textHtml"] = plain_to_html(plain, st)
    box["textFormat"] = TEXT_FORMAT_HTML


def box_text_html(box: dict[str, Any], style: dict[str, Any] | None = None) -> str:
    if str(box.get("textFormat") or "") == TEXT_FORMAT_HTML:
        raw = str(box.get("textHtml") or "").strip()
        if raw:
            return raw
    st = style if style is not None else (box.get("style") or {})
    return plain_to_html(str(box.get("text") or ""), st)


def box_plain_text(box: dict[str, Any]) -> str:
    return str(box.get("text") or "")


def mark_box_html(box: dict[str, Any], html: str, plain: str) -> None:
    box["textHtml"] = str(html or "")
    box["text"] = str(plain or "")
    box["textFormat"] = TEXT_FORMAT_HTML


def html_body_for_label(full_html: str) -> str:
    """QLabel 表示用に body 内を抽出（なければそのまま）。"""
    raw = str(full_html or "").strip()
    if not raw:
        return ""
    m = re.search(r"<body[^>]*>(.*)</body>", raw, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw


def html_for_pdf_box(full_html: str, default_style: dict[str, Any]) -> str:
    """PyMuPDF insert_htmlbox 向けの簡易 HTML。"""
    tc = str(default_style.get("textColor") or "#111827")
    fs = float(default_style.get("fontSize") or 14)
    ls = float(default_style.get("lineSpacing") or fs)
    body = html_body_for_label(full_html)
    if not body:
        return ""
    return (
        f'<div style="font-family: Meiryo, sans-serif; color: {tc}; '
        f'font-size: {fs}pt; line-height: {ls}pt; margin: 0; padding: 0;">{body}</div>'
    )
