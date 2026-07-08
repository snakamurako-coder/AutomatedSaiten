"""定型文（フレーズシール）テンプレートの永続化（config.json）。"""

from __future__ import annotations

import copy
import html as html_lib
import re
import uuid
from typing import Any

from config import load_config, save_config
from models.text_annotation_repo import TEXT_STYLE_TEMPLATE_A, resolve_text_style
from ui_qt.floating_palette.text_rich import (
    TEXT_FORMAT_HTML,
    box_has_saved_html,
    box_text_html,
    clip_rich_html_lines,
    display_width,
    extract_primary_text_color,
    html_body_for_label,
    palette_styled_html,
    plain_to_palette_html,
    sanitize_html_for_palette,
    sync_box_html_from_style,
    truncate_display_width,
    truncate_rich_html_by_width,
)

PHRASE_SIMPLE_COUNT = 6
PHRASE_UNREGISTERED_LABEL = "（未登録）"
PHRASE_SIMPLE_TEXT_WIDTH = 30  # 全角15文字相当（半角30文字）
PHRASE_DETAIL_MAX_LINES = 3
PHRASE_DETAIL_LINE_WIDTH = 30  # 全角15文字相当（半角30文字）
PHRASE_DETAIL_ELLIPSIS = "..."

_DEFAULT_PHRASE_TEXTS: tuple[str, ...] = (
    "〇",
    "△",
    "再確認",
    "良い点",
    "改善点",
    "もう一度",
)


def _default_templates() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for text in _DEFAULT_PHRASE_TEXTS:
        out.append(
            {
                "id": str(uuid.uuid4()),
                "label": text,
                "text": text,
                "textHtml": "",
                "textFormat": "plain",
                "style": dict(TEXT_STYLE_TEMPLATE_A),
                "width": 120.0,
                "height": 36.0,
            }
        )
    return out


def _normalize_template(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "")
    plain = text.replace("\n", " ").strip()
    label = str(raw.get("label") or "").strip() or (plain[:20] if plain else "")
    style = resolve_text_style(raw.get("style") or {})
    tid = str(raw.get("id") or "").strip() or str(uuid.uuid4())
    return {
        "id": tid,
        "label": label[:40],
        "text": text,
        "textHtml": str(raw.get("textHtml") or ""),
        "textFormat": str(raw.get("textFormat") or "plain"),
        "style": style,
        "width": max(40.0, float(raw.get("width") or 120.0)),
        "height": max(24.0, float(raw.get("height") or 36.0)),
    }


def load_phrase_templates() -> list[dict[str, Any]]:
    cfg = load_config()
    raw = cfg.get("phrase_templates")
    if not isinstance(raw, dict):
        return _default_templates()
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        return _default_templates()
    out: list[dict[str, Any]] = []
    for item in items:
        norm = _normalize_template(item)
        if norm is not None:
            out.append(norm)
    return out if out else _default_templates()


def save_phrase_templates(templates: list[dict[str, Any]]) -> None:
    items = []
    for tpl in templates:
        norm = _normalize_template(tpl)
        if norm is not None:
            items.append(norm)
    if not items:
        items = _default_templates()
    cfg = load_config()
    raw = cfg.get("phrase_templates")
    merged: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    merged["items"] = items
    cfg["phrase_templates"] = merged
    save_config(cfg)


def load_recent_phrase_ids() -> list[str]:
    cfg = load_config()
    raw = cfg.get("phrase_templates")
    if not isinstance(raw, dict):
        return []
    recent = raw.get("recent_ids")
    if not isinstance(recent, list):
        return []
    return [str(x) for x in recent if str(x).strip()]


def save_recent_phrase_ids(ids: list[str]) -> None:
    cfg = load_config()
    raw = cfg.get("phrase_templates")
    merged: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    merged["recent_ids"] = [str(x) for x in ids if str(x).strip()]
    cfg["phrase_templates"] = merged
    save_config(cfg)


def touch_recent_phrase(phrase_id: str) -> None:
    pid = str(phrase_id or "").strip()
    if not pid:
        return
    recent = [x for x in load_recent_phrase_ids() if x != pid]
    recent.insert(0, pid)
    save_recent_phrase_ids(recent[:50])


def phrase_templates_mru() -> list[dict[str, Any]]:
    templates = load_phrase_templates()
    by_id = {str(t["id"]): t for t in templates}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pid in load_recent_phrase_ids():
        tpl = by_id.get(pid)
        if tpl is not None and pid not in seen:
            ordered.append(tpl)
            seen.add(pid)
    for tpl in templates:
        pid = str(tpl["id"])
        if pid not in seen:
            ordered.append(tpl)
            seen.add(pid)
    return ordered


def phrase_has_content(tpl: dict[str, Any]) -> bool:
    plain = str(tpl.get("text") or "").strip()
    if plain:
        return True
    html = str(tpl.get("textHtml") or "").strip()
    if not html:
        return False
    body = html_body_for_label(html)
    stripped = re.sub(r"<[^>]+>", "", body or "").replace("&nbsp;", " ").strip()
    return bool(stripped)


def phrase_detail_display_lines(
    text: str,
    *,
    max_lines: int = PHRASE_DETAIL_MAX_LINES,
    line_width: int = PHRASE_DETAIL_LINE_WIDTH,
) -> list[str]:
    lines = str(text or "").split("\n")
    return [
        truncate_display_width(line, line_width, ellipsis=PHRASE_DETAIL_ELLIPSIS)
        for line in lines[:max_lines]
    ]


def phrase_text_one_line(tpl: dict[str, Any]) -> str:
    text = phrase_preview_text(tpl)
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def phrase_display_style(tpl: dict[str, Any]) -> dict[str, Any]:
    """パレット表示用の書式（HTML 内の文字色を style に反映）。"""
    st = resolve_text_style(tpl.get("style") or {})
    if box_has_saved_html(tpl):
        tc = extract_primary_text_color(str(tpl.get("textHtml") or ""))
        if tc:
            st = {**st, "textColor": tc}
    return st


def _phrase_palette_rich_html(
    tpl: dict[str, Any],
    *,
    one_line: bool = False,
    detail: bool = False,
    align_left: bool = False,
    truncate_width: int | None = None,
    max_lines: int | None = None,
    line_width: int | None = None,
) -> str:
    st = phrase_display_style(tpl)
    if not box_has_saved_html(tpl):
        plain = phrase_text_one_line(tpl) if one_line else phrase_preview_text(tpl)
        if truncate_width is not None and one_line:
            plain = truncate_display_width(plain, truncate_width)
        elif max_lines is not None and not one_line:
            lines = phrase_detail_display_lines(
                plain,
                max_lines=max_lines,
                line_width=line_width or PHRASE_DETAIL_LINE_WIDTH,
            )
            inner = "<br>".join(html_lib.escape(line) for line in lines)
            return palette_styled_html(inner, st, detail=detail)
        return plain_to_palette_html(
            plain, st, one_line=one_line, detail=detail, align_left=align_left
        )

    body = html_body_for_label(box_text_html(tpl, st))
    if not body:
        plain = phrase_text_one_line(tpl) if one_line else phrase_preview_text(tpl)
        return plain_to_palette_html(
            plain, st, one_line=one_line, detail=detail, align_left=align_left
        )

    sanitized = sanitize_html_for_palette(
        body, one_line=one_line, align_left=align_left, detail=detail
    )
    if max_lines is not None:
        sanitized = clip_rich_html_lines(
            sanitized,
            max_lines,
            line_width or PHRASE_DETAIL_LINE_WIDTH,
            ellipsis=PHRASE_DETAIL_ELLIPSIS,
        )
        sanitized = sanitize_html_for_palette(
            sanitized, one_line=False, align_left=align_left, detail=detail
        )
    elif truncate_width is not None:
        sanitized = truncate_rich_html_by_width(sanitized, truncate_width)
        sanitized = sanitize_html_for_palette(
            sanitized, one_line=one_line, align_left=align_left, detail=detail
        )

    return palette_styled_html(
        sanitized, st, detail=detail, align_left=align_left, omit_color=True
    )


def phrase_palette_detail_html(tpl: dict[str, Any]) -> str:
    """詳細タブ用：最大3行・行ごとに幅制限・固定フォント。"""
    if not phrase_has_content(tpl):
        return plain_to_palette_html(
            "（文言未登録）", None, detail=True, one_line=True
        )
    return _phrase_palette_rich_html(
        tpl,
        detail=True,
        max_lines=PHRASE_DETAIL_MAX_LINES,
        line_width=PHRASE_DETAIL_LINE_WIDTH,
    )


def phrase_palette_content_html(
    tpl: dict[str, Any],
    *,
    one_line: bool = False,
    truncate_width: int | None = None,
) -> str:
    if not phrase_has_content(tpl):
        return plain_to_palette_html(
            PHRASE_UNREGISTERED_LABEL, None, one_line=True, align_left=one_line
        )
    return _phrase_palette_rich_html(
        tpl,
        one_line=one_line,
        align_left=one_line,
        truncate_width=truncate_width if one_line else None,
    )


def phrase_simple_button_label(tpl: dict[str, Any]) -> str:
    if not phrase_has_content(tpl):
        return PHRASE_UNREGISTERED_LABEL
    return truncate_display_width(phrase_text_one_line(tpl), PHRASE_SIMPLE_TEXT_WIDTH)


def phrase_detail_body_text(tpl: dict[str, Any]) -> str:
    if not phrase_has_content(tpl):
        return "（文言未登録）"
    return phrase_preview_text(tpl)


def phrase_display_label(tpl: dict[str, Any], *, compact: bool = False) -> str:
    if not phrase_has_content(tpl):
        return PHRASE_UNREGISTERED_LABEL
    if compact:
        return phrase_simple_button_label(tpl)
    label = str(tpl.get("label") or "").strip()
    text = str(tpl.get("text") or "").strip()
    return label or text.replace("\n", " ")[:40] or PHRASE_UNREGISTERED_LABEL


def phrase_preview_text(tpl: dict[str, Any]) -> str:
    plain = str(tpl.get("text") or "").strip()
    if plain:
        return plain
    html = box_text_html(tpl, tpl.get("style"))
    body = html_body_for_label(html)
    return re.sub(r"<[^>]+>", "", body or "").replace("&nbsp;", " ").strip()


def apply_phrase_template_to_box(box: dict[str, Any], template: dict[str, Any]) -> None:
    style = copy.deepcopy(template.get("style") or {})
    box["style"] = style
    box["text"] = str(template.get("text") or "")
    box["textHtml"] = str(template.get("textHtml") or "")
    box["textFormat"] = str(template.get("textFormat") or "plain")
    if not box_has_saved_html(box):
        sync_box_html_from_style(box)


def phrase_template_to_box(tpl: dict[str, Any]) -> dict[str, Any]:
    from models.text_annotation_repo import new_text_box

    box = new_text_box(
        0.0,
        0.0,
        width=max(40.0, float(tpl.get("width") or 120.0)),
        height=max(24.0, float(tpl.get("height") or 36.0)),
    )
    apply_phrase_template_to_box(box, tpl)
    return box


def phrase_updates_from_box(phrase_id: str, box: dict[str, Any]) -> dict[str, Any]:
    text = str(box.get("text") or "")
    label = text.replace("\n", " ").strip()[:20]
    style = resolve_text_style(copy.deepcopy(box.get("style") or {}))
    html = str(box.get("textHtml") or "")
    fmt = str(box.get("textFormat") or "plain")
    if html.strip():
        fmt = TEXT_FORMAT_HTML
        tc = extract_primary_text_color(html)
        if tc:
            style["textColor"] = tc
    return {
        "text": text,
        "label": label,
        "textHtml": html,
        "textFormat": fmt,
        "style": style,
        "width": max(40.0, float(box.get("width") or 120.0)),
        "height": max(24.0, float(box.get("height") or 36.0)),
    }


def phrase_from_text_box(box: dict[str, Any]) -> dict[str, Any]:
    plain = str(box.get("text") or "").strip()
    label = plain.replace("\n", " ")[:20]
    return {
        "id": str(uuid.uuid4()),
        "label": label,
        "text": str(box.get("text") or ""),
        "textHtml": str(box.get("textHtml") or ""),
        "textFormat": str(box.get("textFormat") or "plain"),
        "style": resolve_text_style(copy.deepcopy(box.get("style") or {})),
        "width": max(40.0, float(box.get("width") or 120.0)),
        "height": max(24.0, float(box.get("height") or 36.0)),
    }


def add_phrase_template(template: dict[str, Any]) -> dict[str, Any]:
    norm = _normalize_template(template)
    if norm is None:
        raise ValueError("invalid phrase template")
    templates = load_phrase_templates()
    templates.append(norm)
    save_phrase_templates(templates)
    touch_recent_phrase(str(norm["id"]))
    return norm


def update_phrase_template(phrase_id: str, **updates: Any) -> dict[str, Any] | None:
    pid = str(phrase_id or "").strip()
    if not pid:
        return None
    templates = load_phrase_templates()
    for i, tpl in enumerate(templates):
        if str(tpl.get("id")) != pid:
            continue
        merged = {**tpl, **updates}
        norm = _normalize_template(merged)
        if norm is None:
            return None
        templates[i] = norm
        save_phrase_templates(templates)
        return norm
    return None


def delete_phrase_template(phrase_id: str) -> None:
    pid = str(phrase_id or "").strip()
    if not pid:
        return
    templates = [t for t in load_phrase_templates() if str(t.get("id")) != pid]
    save_phrase_templates(templates)
    save_recent_phrase_ids([x for x in load_recent_phrase_ids() if x != pid])
