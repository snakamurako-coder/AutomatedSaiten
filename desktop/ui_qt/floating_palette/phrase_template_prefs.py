"""定型文（フレーズシール）テンプレートの永続化（config.json）。"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from config import load_config, save_config
from models.text_annotation_repo import TEXT_STYLE_TEMPLATE_A, resolve_text_style
from ui_qt.floating_palette.text_rich import (
    TEXT_FORMAT_HTML,
    box_has_saved_html,
    box_text_html,
    html_body_for_label,
    sync_box_html_from_style,
)

PHRASE_SIMPLE_COUNT = 6
PHRASE_UNREGISTERED_LABEL = "（未登録）"
PHRASE_SIMPLE_TEXT_WIDTH = 30  # 全角15文字相当（半角30文字）

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


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 1 if ord(ch) < 128 else 2
    return width


def truncate_display_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _display_width(text) <= max_width:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        ch_w = 1 if ord(ch) < 128 else 2
        if used + ch_w > max_width - 2:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + "…"


def phrase_text_one_line(tpl: dict[str, Any]) -> str:
    text = phrase_preview_text(tpl)
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def phrase_style_summary(tpl: dict[str, Any]) -> str:
    style = resolve_text_style(tpl.get("style") or {})
    font_size = int(style.get("fontSize") or 14)
    line_spacing = int(style.get("lineSpacing") or font_size)
    newline_count = str(tpl.get("text") or "").count("\n")
    parts = [f"{font_size}pt", f"行{line_spacing}"]
    if newline_count > 0:
        parts.append(f"改{newline_count}")
    return "·".join(parts)


def phrase_simple_button_label(tpl: dict[str, Any]) -> str:
    if not phrase_has_content(tpl):
        return PHRASE_UNREGISTERED_LABEL
    text = truncate_display_width(
        phrase_text_one_line(tpl), PHRASE_SIMPLE_TEXT_WIDTH
    )
    return f"{text}  {phrase_style_summary(tpl)}"


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
    html = str(box.get("textHtml") or "")
    fmt = str(box.get("textFormat") or "plain")
    if html.strip():
        fmt = TEXT_FORMAT_HTML
    return {
        "text": text,
        "label": label,
        "textHtml": html,
        "textFormat": fmt,
        "style": resolve_text_style(copy.deepcopy(box.get("style") or {})),
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
