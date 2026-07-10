"""テキストボックスのリッチテキスト（HTML）ヘルパー。"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

from models.text_annotation_repo import DEFAULT_TEXT_COLOR, DEFAULT_TEXT_STYLE

TEXT_FORMAT_PLAIN = "plain"
TEXT_FORMAT_HTML = "html"

TEXT_ALIGN_H_VALUES = ("left", "center", "right")
TEXT_ALIGN_V_VALUES = ("top", "center", "bottom")


def normalize_text_align(style: dict[str, Any] | None) -> tuple[str, str]:
    st = style or {}
    h = str(st.get("textAlignH") or "left").lower()
    v = str(st.get("textAlignV") or "top").lower()
    if h not in TEXT_ALIGN_H_VALUES:
        h = "left"
    if v not in TEXT_ALIGN_V_VALUES:
        v = "top"
    return h, v


def css_text_align(style: dict[str, Any] | None) -> str:
    return normalize_text_align(style)[0]


def qt_horizontal_alignment(style: dict[str, Any] | None) -> int:
    from PySide6.QtCore import Qt

    h, _ = normalize_text_align(style)
    return {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[h]


def qt_label_alignment(style: dict[str, Any] | None) -> int:
    from PySide6.QtCore import Qt

    h, v = normalize_text_align(style)
    h_align = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[h]
    v_align = {
        "top": Qt.AlignmentFlag.AlignTop,
        "center": Qt.AlignmentFlag.AlignVCenter,
        "bottom": Qt.AlignmentFlag.AlignBottom,
    }[v]
    return h_align | v_align


def plain_to_html(text: str, style: dict[str, Any] | None) -> str:
    st = style or {}
    tc = str(st.get("textColor") or DEFAULT_TEXT_COLOR)
    fs = float(st.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14)
    ls = float(
        st.get("lineSpacing")
        or DEFAULT_TEXT_STYLE.get("lineSpacing")
        or 20
    )
    body = html_lib.escape(str(text or "")).replace("\n", "<br>")
    p_styles = [
        "margin-top:0",
        "margin-bottom:0",
        f"color:{tc}",
        f"font-size:{fs}pt",
        f"line-height:{ls}pt",
        f"text-align:{css_text_align(st)}",
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


def box_has_saved_html(box: dict[str, Any]) -> bool:
    return (
        str(box.get("textFormat") or "") == TEXT_FORMAT_HTML
        and bool(str(box.get("textHtml") or "").strip())
    )


def html_body_for_label(full_html: str) -> str:
    """QLabel 表示用に body 内を抽出（なければそのまま）。"""
    raw = str(full_html or "").strip()
    if not raw:
        return ""
    m = re.search(r"<body[^>]*>(.*)</body>", raw, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw


def html_for_pdf_box(
    full_html: str,
    default_style: dict[str, Any],
    *,
    box_height: float | None = None,
) -> str:
    """PyMuPDF insert_htmlbox 向けの簡易 HTML。"""
    tc = str(default_style.get("textColor") or DEFAULT_TEXT_COLOR)
    fs = float(default_style.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14)
    ls = float(
        default_style.get("lineSpacing")
        or DEFAULT_TEXT_STYLE.get("lineSpacing")
        or 20
    )
    align = css_text_align(default_style)
    _, v_align = normalize_text_align(default_style)
    body = html_body_for_label(full_html)
    if not body:
        return ""
    outer_styles = [
        "font-family: Meiryo, sans-serif",
        f"color: {tc}",
        f"font-size: {fs}pt",
        f"line-height: {ls}pt",
        f"text-align: {align}",
        "margin: 0",
        "padding: 0",
    ]
    if box_height and box_height > 0:
        outer_styles.append(f"min-height: {box_height}pt")
        if v_align == "center":
            outer_styles.append("display: flex")
            outer_styles.append("flex-direction: column")
            outer_styles.append("justify-content: center")
        elif v_align == "bottom":
            outer_styles.append("display: flex")
            outer_styles.append("flex-direction: column")
            outer_styles.append("justify-content: flex-end")
    style_attr = "; ".join(outer_styles)
    return f'<div style="{style_attr};">{body}</div>'


_PALETTE_FONT_SIZE = "12px"
_PALETTE_LINE_HEIGHT = "15px"
_PALETTE_DETAIL_FONT_SIZE = "11px"
_PALETTE_DETAIL_LINE_HEIGHT = "15px"


def _hex_rgb_tuple(hex_color: str) -> tuple[int, int, int]:
    h = str(hex_color or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (255, 255, 255)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def palette_fill_background(style: dict[str, Any] | None) -> str:
    from models.text_annotation_repo import resolve_text_style

    st = resolve_text_style(style)
    fa = float(st.get("fillAlpha", 0))
    if fa <= 0:
        return "transparent"
    r, g, b = _hex_rgb_tuple(str(st.get("fillColor") or "#ffffff"))
    return f"rgba({r}, {g}, {b}, {fa})"


def palette_border_css(style: dict[str, Any] | None) -> str:
    from models.text_annotation_repo import resolve_text_style

    st = resolve_text_style(style)
    bw = float(st.get("borderWidth") or 0)
    ba = float(st.get("borderAlpha") or 0)
    if bw <= 0 or ba <= 0:
        return "none"
    r, g, b = _hex_rgb_tuple(
        str(st.get("borderColor") or st.get("textColor") or DEFAULT_TEXT_COLOR)
    )
    return f"{bw}px solid rgba({r}, {g}, {b}, {ba})"


def extract_primary_text_color(html: str) -> str | None:
    """リッチ HTML から主要な文字色を抽出する。"""
    raw = str(html or "").strip()
    if not raw:
        return None
    body = html_body_for_label(raw) if "<body" in raw.lower() else raw
    hex_matches = re.findall(r"color:\s*(#[0-9a-fA-F]{6})\b", body, flags=re.IGNORECASE)
    if hex_matches:
        return hex_matches[0].lower()
    rgb_matches = re.findall(
        r"color:\s*rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
        body,
        flags=re.IGNORECASE,
    )
    if rgb_matches:
        r, g, b = (int(x) for x in rgb_matches[0])
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def palette_styled_html(
    inner_html: str,
    style: dict[str, Any] | None,
    *,
    detail: bool = False,
    align_left: bool = False,
    omit_color: bool = False,
) -> str:
    st = style or {}
    tc = str(st.get("textColor") or DEFAULT_TEXT_COLOR)
    fs = _PALETTE_DETAIL_FONT_SIZE if detail else _PALETTE_FONT_SIZE
    lh = _PALETTE_DETAIL_LINE_HEIGHT if detail else _PALETTE_LINE_HEIGHT
    align = "left" if align_left else css_text_align(st)
    span_styles = [
        "margin:0",
        "padding:0",
        f"font-size:{fs}",
        f"line-height:{lh}",
        f"text-align:{align}",
        "font-family:Meiryo, sans-serif",
    ]
    if not omit_color:
        span_styles.insert(2, f"color:{tc}")
        if str(st.get("fontWeight") or "") == "bold":
            span_styles.append("font-weight:bold")
        if str(st.get("fontStyle") or "") == "italic":
            span_styles.append("font-style:italic")
        if str(st.get("textDecoration") or "") == "underline":
            span_styles.append("text-decoration:underline")
    span_style = "; ".join(span_styles)
    return f'<span style="{span_style}">{inner_html}</span>'


def plain_to_palette_html(
    text: str,
    style: dict[str, Any] | None,
    *,
    one_line: bool = False,
    detail: bool = False,
    align_left: bool = False,
) -> str:
    body_text = str(text or "")
    if one_line:
        body_text = re.sub(r"\s+", " ", body_text.replace("\n", " ")).strip()
        body = html_lib.escape(body_text)
    else:
        body = html_lib.escape(body_text).replace("\n", "<br>")
    return palette_styled_html(body, style, detail=detail, align_left=align_left)


def display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 1 if ord(ch) < 128 else 2
    return width


def truncate_display_width(
    text: str, max_width: int, ellipsis: str = "…"
) -> str:
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    ell_w = display_width(ellipsis)
    out: list[str] = []
    used = 0
    for ch in text:
        ch_w = 1 if ord(ch) < 128 else 2
        if used + ch_w > max_width - ell_w:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + ellipsis


def _strip_html_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").replace("&nbsp;", " ")


def _split_html_lines(fragment: str) -> list[str]:
    text = re.sub(r"</p>\s*<p[^>]*>", "<br>", fragment, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    parts = re.split(r"<br\s*/?>", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts]


def _clean_qt_html_fragment(html: str) -> str:
    text = re.sub(r"<!--.*?-->", "", str(html or ""), flags=re.DOTALL)
    text = html_body_for_label(text) or text
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    return text.strip()


def truncate_rich_html_by_width(
    html_fragment: str,
    max_width: int,
    *,
    ellipsis: str = "…",
) -> str:
    fragment = str(html_fragment or "").strip()
    if not fragment or max_width <= 0:
        return fragment
    plain_one = re.sub(
        r"\s+", " ", _strip_html_tags(fragment).replace("\n", " ")
    ).strip()
    if display_width(plain_one) <= max_width:
        return fragment

    from PySide6.QtGui import QTextCursor, QTextDocument

    doc = QTextDocument()
    doc.setHtml(f"<html><body>{fragment}</body></html>")
    plain = doc.toPlainText()
    plain_one = re.sub(r"\s+", " ", plain.replace("\n", " ")).strip()
    if display_width(plain_one) <= max_width:
        return fragment

    cut_plain = truncate_display_width(plain_one, max_width, ellipsis=ellipsis)
    end_chars = len(cut_plain)
    if cut_plain.endswith(ellipsis):
        end_chars -= len(ellipsis)
    end_chars = max(0, end_chars)
    # plain 文字数と QTextDocument の有効 position は一致しない場合がある。
    max_pos = max(0, doc.characterCount() - 1)
    end_chars = min(end_chars, max_pos)

    cur = QTextCursor(doc)
    cur.setPosition(end_chars)
    cur.setPosition(0, QTextCursor.MoveMode.KeepAnchor)
    body = _clean_qt_html_fragment(cur.selection().toHtml())
    if not body:
        return html_lib.escape(cut_plain)
    plain_body = _strip_html_tags(body)
    if cut_plain.endswith(ellipsis) and not plain_body.endswith(ellipsis):
        body = f"{body}{html_lib.escape(ellipsis)}"
    return body.strip()


def clip_rich_html_lines(
    html_fragment: str,
    max_lines: int,
    line_width: int,
    *,
    ellipsis: str = "...",
) -> str:
    fragment = str(html_fragment or "").strip()
    if not fragment or max_lines <= 0:
        return fragment
    parts = _split_html_lines(fragment)[:max_lines]
    out: list[str] = []
    for part in parts:
        if not part:
            out.append("")
            continue
        plain = _strip_html_tags(part)
        if display_width(plain) > line_width:
            part = truncate_rich_html_by_width(part, line_width, ellipsis=ellipsis)
        out.append(part)
    return "<br>".join(out)


def strip_canvas_font_styles(html: str) -> str:
    """記述欄キャンバス表示用: inline の font-size / line-height を除去（QFont に任せる）。"""
    text = str(html or "")
    text = re.sub(r"font-size:\s*[^;\"']+;?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"line-height:\s*[^;\"']+;?", "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s*font point-size="[^"]*"', "", text, flags=re.IGNORECASE)
    text = re.sub(r'font-size="[^"]*"', "", text, flags=re.IGNORECASE)
    text = re.sub(r"<font[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</font>", "", text, flags=re.IGNORECASE)
    return text


def html_for_canvas_display(html: str) -> str:
    """ズーム付きキャンバス上の QTextEdit 読み込み向け HTML（inline サイズ除去）。"""
    return strip_canvas_font_styles(html)


def scaled_canvas_text_style(
    style: dict[str, Any] | None,
    display_scale: float,
) -> dict[str, Any]:
    """キャンバス表示向け style（fontSize / lineSpacing を display_scale で補正）。"""
    st = dict(style or {})
    scale = max(0.01, float(display_scale))
    base_fs = float(st.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14)
    base_ls = float(
        st.get("lineSpacing")
        or DEFAULT_TEXT_STYLE.get("lineSpacing")
        or 20
    )
    st["fontSize"] = base_fs / scale
    st["lineSpacing"] = base_ls / scale
    return st


def _paragraph_style_attr(st: dict[str, Any], *, include_color: bool = True) -> str:
    fs = float(st.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14)
    ls = float(
        st.get("lineSpacing")
        or DEFAULT_TEXT_STYLE.get("lineSpacing")
        or 20
    )
    p_styles = [
        "margin-top:0",
        "margin-bottom:0",
        f"font-size:{fs:g}pt",
        f"line-height:{ls:g}pt",
        f"text-align:{css_text_align(st)}",
        "font-family:Meiryo,sans-serif",
    ]
    if include_color:
        tc = str(st.get("textColor") or DEFAULT_TEXT_COLOR)
        p_styles.insert(2, f"color:{tc}")
    if str(st.get("fontWeight") or "") == "bold":
        p_styles.append("font-weight:bold")
    if str(st.get("fontStyle") or "") == "italic":
        p_styles.append("font-style:italic")
    if str(st.get("textDecoration") or "") == "underline":
        p_styles.append("text-decoration:underline")
    return "; ".join(p_styles)


def _upsert_css_property(style: str, prop: str, value: str) -> str:
    text = re.sub(rf"{re.escape(prop)}:\s*[^;\"']+;?", "", style, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip().strip(";")
    inserted = f"{prop}:{value}"
    return f"{inserted}; {text}" if text else inserted


def _collect_html_colors(fragment: str) -> set[str]:
    colors: set[str] = set()
    for match in re.finditer(r"color:\s*(#[0-9a-fA-F]{3,8})\b", fragment, flags=re.IGNORECASE):
        colors.add(match.group(1).lower())
    for match in re.finditer(
        r"color:\s*rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
        fragment,
        flags=re.IGNORECASE,
    ):
        r, g, b = (int(match.group(i)) for i in range(1, 4))
        colors.add(f"#{r:02x}{g:02x}{b:02x}")
    return colors


def html_has_rich_character_styles(html: str) -> bool:
    """文字ごとの色・装飾があるリッチ HTML か（plain_to_html で潰さない判定）。"""
    raw = str(html or "").strip()
    if not raw:
        return False
    body = html_body_for_label(raw) if re.search(r"<body\b", raw, re.IGNORECASE) else raw
    stripped = strip_canvas_font_styles(body)
    if re.search(r"<(span|b|i|u|strong|em|font)\b", stripped, re.IGNORECASE):
        return True
    return len(_collect_html_colors(stripped)) > 1


def _apply_display_font_sizes_to_html(html: str, disp_st: dict[str, Any]) -> str:
    """既存の文字色・装飾を保ちつつ font-size / line-height だけ表示倍率で上書き。"""
    fs = float(disp_st.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14)
    ls = float(
        disp_st.get("lineSpacing")
        or DEFAULT_TEXT_STYLE.get("lineSpacing")
        or 20
    )
    text = strip_canvas_font_styles(str(html or "").strip())
    if not text:
        return ""

    def fix_style_attr(match: re.Match[str]) -> str:
        quote, style = match.group(1), match.group(2)
        style = _upsert_css_property(style, "font-size", f"{fs:g}pt")
        style = _upsert_css_property(style, "line-height", f"{ls:g}pt")
        if not re.search(r"font-family\s*:", style, re.IGNORECASE):
            style = _upsert_css_property(style, "font-family", "Meiryo,sans-serif")
        return f"style={quote}{style}{quote}"

    text = re.sub(r'style=(["\'])([^"\']*)\1', fix_style_attr, text, flags=re.IGNORECASE)

    def fix_bare_p_tag(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        if re.search(r"\bstyle\s*=", attrs, re.IGNORECASE):
            return match.group(0)
        p_style = (
            f"font-size:{fs:g}pt; line-height:{ls:g}pt; "
            "font-family:Meiryo,sans-serif"
        )
        return f'<p style="{p_style}"{attrs}>'

    text = re.sub(r"<p\b([^>]*)>", fix_bare_p_tag, text, flags=re.IGNORECASE)
    if not re.search(r"<p\b", text, re.IGNORECASE):
        p_style = (
            f"font-size:{fs:g}pt; line-height:{ls:g}pt; "
            "font-family:Meiryo,sans-serif"
        )
        return f'<p style="{p_style}">{text}</p>'
    return text


def _restyle_paragraphs_html(
    inner: str,
    disp_st: dict[str, Any],
    *,
    include_color: bool = True,
) -> str:
    """複数 <p> に表示用スタイルを付与する。"""
    text = strip_canvas_font_styles(str(inner or "").strip())
    if not text:
        return ""
    if not include_color:
        return _apply_display_font_sizes_to_html(text, disp_st)
    p_style = _paragraph_style_attr(disp_st, include_color=True)
    if re.search(r"<p\b", text, re.I):
        return re.sub(r"<p[^>]*>", f'<p style="{p_style}">', text, flags=re.I)
    return f'<p style="{p_style}">{text}</p>'


def build_canvas_label_html(
    box: dict[str, Any],
    style: dict[str, Any] | None,
    *,
    display_scale: float,
    editor_plain: str = "",
) -> str:
    """QLabel 表示用 HTML。<p> に表示倍率付き font-size を直接付与する。"""
    st = style or {}
    disp_st = scaled_canvas_text_style(st, display_scale)
    saved_html = box_text_html(box, st)
    if html_has_rich_character_styles(saved_html):
        body = html_body_for_label(html_for_canvas_display(saved_html))
        if html_has_visible_text(body):
            return _apply_display_font_sizes_to_html(body, disp_st)
    plain = str(box.get("text") or editor_plain or "")
    if plain.strip():
        return html_body_for_label(plain_to_html(plain, disp_st))
    body = label_body_from_box_content(box, st, editor_plain=editor_plain)
    if not body:
        return ""
    return _restyle_paragraphs_html(body, disp_st, include_color=True)


def build_canvas_editor_html(
    box: dict[str, Any],
    style: dict[str, Any] | None,
    *,
    display_scale: float,
    editor_plain: str = "",
) -> str:
    """QTextEdit 読み込み用のフル HTML 文書。"""
    label = build_canvas_label_html(
        box,
        style,
        display_scale=display_scale,
        editor_plain=editor_plain,
    )
    if not label:
        return '<html><head></head><body style="margin:0; padding:0;"></body></html>'
    return (
        '<html><head></head><body style="margin:0; padding:0;">'
        f"{label}</body></html>"
    )


def html_has_visible_text(fragment: str) -> bool:
    plain = _strip_html_tags(str(fragment or "")).replace("\xa0", " ").strip()
    return bool(plain)


def label_body_from_box_content(
    box: dict[str, Any],
    style: dict[str, Any] | None,
    *,
    editor_plain: str = "",
) -> str:
    """QLabel 表示用 body。HTML に可視文字がなければ plain text へフォールバック。"""
    html = html_for_canvas_display(box_text_html(box, style))
    body = html_body_for_label(html)
    if html_has_visible_text(body):
        return body
    plain = str(box.get("text") or editor_plain or "").strip()
    if plain:
        return html_lib.escape(plain).replace("\n", "<br>")
    return ""


def _strip_palette_font_styles(html: str) -> str:
    return strip_canvas_font_styles(html)


def _apply_palette_font_to_inline_styles(html: str, *, detail: bool) -> str:
    fs = _PALETTE_DETAIL_FONT_SIZE if detail else _PALETTE_FONT_SIZE
    lh = _PALETTE_DETAIL_LINE_HEIGHT if detail else _PALETTE_LINE_HEIGHT

    def fix_style(match: re.Match[str]) -> str:
        style = _strip_palette_font_styles(match.group(1))
        style = re.sub(r"\s+", " ", style).strip().strip(";").strip()
        prefix = f"font-size:{fs}; line-height:{lh}"
        if style:
            return f'style="{prefix}; {style}"'
        return f'style="{prefix}"'

    return re.sub(r'style="([^"]*)"', fix_style, html, flags=re.IGNORECASE)


def _flatten_block_tags_for_palette(html: str) -> str:
    text = re.sub(r"</p>\s*<p[^>]*>", "<br>", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    return text


def sanitize_html_for_palette(
    html: str,
    *,
    one_line: bool,
    align_left: bool = False,
    detail: bool = False,
) -> str:
    text = str(html or "").strip()
    if not text:
        return ""
    text = _strip_palette_font_styles(text)
    if one_line or detail:
        text = _flatten_block_tags_for_palette(text)
    if one_line:
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    if align_left:
        text = re.sub(
            r"text-align:\s*[^;\"']+;?",
            "text-align:left;",
            text,
            flags=re.IGNORECASE,
        )
        if not re.search(r"text-align:", text, flags=re.IGNORECASE):
            text = f'<span style="text-align:left">{text}</span>'
    if detail:
        text = _apply_palette_font_to_inline_styles(text, detail=True)
        fs = _PALETTE_DETAIL_FONT_SIZE
        lh = _PALETTE_DETAIL_LINE_HEIGHT
        if not re.search(r"font-size:", text, flags=re.IGNORECASE):
            text = f'<span style="font-size:{fs}; line-height:{lh}">{text}</span>'
    return text.strip()


def append_text_to_box(
    box: dict[str, Any],
    fragment: str,
    *,
    position: str = "after",
) -> None:
    """既存ボックス文言の前または後に追記（書式はボックス style を維持）。"""
    chunk = str(fragment or "")
    if not chunk:
        return
    plain = str(box.get("text") or "")
    pos = str(position or "after").lower()
    if pos == "before":
        box["text"] = chunk + plain
    else:
        box["text"] = plain + chunk
    if box_has_saved_html(box) or str(box.get("text") or "").strip():
        sync_box_html_from_style(box)


def replace_box_text(
    box: dict[str, Any],
    text: str,
    *,
    text_html: str | None = None,
    text_format: str | None = None,
) -> None:
    """ボックス文言を完全置換（位置・サイズ・style は維持）。"""
    box["text"] = str(text or "")
    if text_html is not None:
        box["textHtml"] = str(text_html or "")
        box["textFormat"] = str(text_format or TEXT_FORMAT_HTML)
        if not str(box.get("textHtml") or "").strip():
            box["textFormat"] = TEXT_FORMAT_PLAIN
        return
    if str(box.get("text") or "").strip():
        sync_box_html_from_style(box)
    else:
        box["textHtml"] = ""
        box["textFormat"] = TEXT_FORMAT_PLAIN


def replace_box_from_template(box: dict[str, Any], template: dict[str, Any]) -> None:
    replace_box_text(
        box,
        str(template.get("text") or ""),
        text_html=str(template.get("textHtml") or "") or None,
        text_format=str(template.get("textFormat") or "") or None,
    )
    if not box_has_saved_html(box) and str(box.get("text") or "").strip():
        sync_box_html_from_style(box)
