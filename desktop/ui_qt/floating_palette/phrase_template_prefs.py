"""定型文（フレーズシール）テンプレートの永続化（config.json）。"""

from __future__ import annotations

import copy
import html as html_lib
import re
import secrets
import string
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

PHRASE_GROUP_ID_CHARS = string.ascii_letters + string.digits
PHRASE_GROUP_ID_LEN = 8


def _phrase_templates_config() -> dict[str, Any]:
    cfg = load_config()
    raw = cfg.get("phrase_templates")
    return dict(raw) if isinstance(raw, dict) else {}


def _save_phrase_templates_config(merged: dict[str, Any]) -> None:
    cfg = load_config()
    cfg["phrase_templates"] = merged
    save_config(cfg)


def load_used_phrase_group_ids() -> set[str]:
    used = _phrase_templates_config().get("used_phrase_group_ids")
    if not isinstance(used, list):
        return set()
    return {str(x).strip() for x in used if str(x).strip()}


def _register_used_phrase_group_id(group_id: str) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    merged = _phrase_templates_config()
    used = load_used_phrase_group_ids()
    used.add(gid)
    merged["used_phrase_group_ids"] = sorted(used)
    _save_phrase_templates_config(merged)


def _collect_active_phrase_group_ids(templates: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for tpl in templates:
        gid = str(tpl.get("phraseGroupId") or "").strip()
        if gid:
            out.add(gid)
    return out


def _generate_phrase_group_id(*, reserved: set[str] | None = None) -> str:
    taken = set(reserved or set())
    taken |= load_used_phrase_group_ids()
    for _ in range(512):
        gid = "".join(
            secrets.choice(PHRASE_GROUP_ID_CHARS) for _ in range(PHRASE_GROUP_ID_LEN)
        )
        if gid not in taken:
            return gid
    raise RuntimeError("phraseGroupId の生成に失敗しました")


def _ensure_phrase_group_id(
    raw: dict[str, Any],
    *,
    reserved: set[str],
) -> str:
    existing = str(raw.get("phraseGroupId") or "").strip()
    if (
        existing
        and len(existing) == PHRASE_GROUP_ID_LEN
        and all(c in PHRASE_GROUP_ID_CHARS for c in existing)
        and existing not in reserved
    ):
        reserved.add(existing)
        return existing
    gid = _generate_phrase_group_id(reserved=reserved)
    reserved.add(gid)
    return gid

_DEFAULT_PHRASE_TEXTS: tuple[str, ...] = (
    "〇",
    "△",
    "再確認",
    "良い点",
    "改善点",
    "もう一度",
)


def _default_templates() -> list[dict[str, Any]]:
    reserved = load_used_phrase_group_ids()
    out: list[dict[str, Any]] = []
    for text in _DEFAULT_PHRASE_TEXTS:
        norm = _normalize_template(
            {
                "id": str(uuid.uuid4()),
                "label": text,
                "text": text,
                "textHtml": "",
                "textFormat": "plain",
                "style": dict(TEXT_STYLE_TEMPLATE_A),
                "width": 120.0,
                "height": 36.0,
            },
            reserved_group_ids=reserved,
        )
        if norm is not None:
            out.append(norm)
    return out


def _normalize_template(
    raw: Any,
    *,
    reserved_group_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "")
    plain = text.replace("\n", " ").strip()
    label = str(raw.get("label") or "").strip() or (plain[:20] if plain else "")
    style = resolve_text_style(raw.get("style") or {})
    tid = str(raw.get("id") or "").strip() or str(uuid.uuid4())
    reserved = set(reserved_group_ids or set())
    phrase_group_id = _ensure_phrase_group_id(raw, reserved=reserved)
    return {
        "id": tid,
        "phraseGroupId": phrase_group_id,
        "label": label[:40],
        "text": text,
        "textHtml": str(raw.get("textHtml") or ""),
        "textFormat": str(raw.get("textFormat") or "plain"),
        "style": style,
        "width": max(40.0, float(raw.get("width") or 120.0)),
        "height": max(24.0, float(raw.get("height") or 36.0)),
    }


def _migrate_phrase_templates(items: list[Any]) -> list[dict[str, Any]]:
    reserved = load_used_phrase_group_ids()
    out: list[dict[str, Any]] = []
    changed = False
    for item in items:
        before_gid = str(item.get("phraseGroupId") or "").strip() if isinstance(item, dict) else ""
        norm = _normalize_template(item, reserved_group_ids=reserved)
        if norm is None:
            continue
        if str(norm.get("phraseGroupId") or "") != before_gid:
            changed = True
        out.append(norm)
    if changed and out:
        save_phrase_templates(out)
    return out if out else _default_templates()


def load_phrase_templates() -> list[dict[str, Any]]:
    raw = _phrase_templates_config()
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        defaults = _default_templates()
        save_phrase_templates(defaults)
        return defaults
    return _migrate_phrase_templates(items)


def save_phrase_templates(templates: list[dict[str, Any]]) -> None:
    reserved = load_used_phrase_group_ids()
    items: list[dict[str, Any]] = []
    for tpl in templates:
        norm = _normalize_template(tpl, reserved_group_ids=reserved)
        if norm is not None:
            items.append(norm)
    if not items:
        items = _default_templates()
    merged = _phrase_templates_config()
    merged["items"] = items
    _save_phrase_templates_config(merged)


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
    gid = str(template.get("phraseGroupId") or "").strip()
    if gid:
        box["phraseGroupId"] = gid
    tid = str(template.get("id") or "").strip()
    if tid:
        box["phraseTemplateId"] = tid
    if not box_has_saved_html(box):
        sync_box_html_from_style(box)


def apply_phrase_placement_meta(box: dict[str, Any], meta: dict[str, Any] | None) -> None:
    if not isinstance(meta, dict):
        return
    if meta.get("resultId") is not None:
        box["placedResultId"] = int(meta["resultId"])
    if meta.get("fieldId"):
        box["placedFieldId"] = str(meta["fieldId"])
    if meta.get("studentId") is not None:
        box["placedStudentId"] = str(meta.get("studentId") or "")
    if meta.get("studentName") is not None:
        box["placedStudentName"] = str(meta.get("studentName") or "")


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
    reserved = load_used_phrase_group_ids()
    gid = _generate_phrase_group_id(reserved=reserved)
    return {
        "id": str(uuid.uuid4()),
        "phraseGroupId": gid,
        "label": label,
        "text": str(box.get("text") or ""),
        "textHtml": str(box.get("textHtml") or ""),
        "textFormat": str(box.get("textFormat") or "plain"),
        "style": resolve_text_style(copy.deepcopy(box.get("style") or {})),
        "width": max(40.0, float(box.get("width") or 120.0)),
        "height": max(24.0, float(box.get("height") or 36.0)),
    }


def phrase_template_by_group_id(group_id: str) -> dict[str, Any] | None:
    gid = str(group_id or "").strip()
    if not gid:
        return None
    for tpl in load_phrase_templates():
        if str(tpl.get("phraseGroupId") or "") == gid:
            return tpl
    return None


def clone_phrase_content_with_new_group_id(template: dict[str, Any]) -> dict[str, Any]:
    src = dict(template or {})
    reserved = load_used_phrase_group_ids() | {
        str(t.get("phraseGroupId") or "").strip() for t in load_phrase_templates()
    }
    gid = _generate_phrase_group_id(reserved=reserved)
    cloned = {
        "id": str(src.get("id") or str(uuid.uuid4())),
        "phraseGroupId": gid,
        "label": str(src.get("label") or ""),
        "text": str(src.get("text") or ""),
        "textHtml": str(src.get("textHtml") or ""),
        "textFormat": str(src.get("textFormat") or "plain"),
        "style": copy.deepcopy(src.get("style") or {}),
        "width": float(src.get("width") or 120.0),
        "height": float(src.get("height") or 36.0),
    }
    norm = _normalize_template(cloned, reserved_group_ids=reserved)
    if norm is None:
        raise ValueError("invalid phrase template")
    return norm


def template_dict_from_uniform_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = dict(config or {})
    base = {
        "id": str(raw.get("phraseTemplateId") or raw.get("id") or str(uuid.uuid4())),
        "phraseGroupId": str(raw.get("phraseGroupId") or ""),
        "label": str(raw.get("label") or ""),
        "text": str(raw.get("text") or ""),
        "textHtml": str(raw.get("textHtml") or ""),
        "textFormat": str(raw.get("textFormat") or "plain"),
        "style": copy.deepcopy(raw.get("style") or {}),
        "width": float(raw.get("width") or 120.0),
        "height": float(raw.get("height") or 36.0),
    }
    norm = _normalize_template(base)
    if norm is None:
        raise ValueError("invalid uniform feedback config")
    return norm


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
    templates = load_phrase_templates()
    removed_gid = ""
    kept: list[dict[str, Any]] = []
    for tpl in templates:
        if str(tpl.get("id")) == pid:
            removed_gid = str(tpl.get("phraseGroupId") or "").strip()
            continue
        kept.append(tpl)
    if removed_gid:
        _register_used_phrase_group_id(removed_gid)
    save_phrase_templates(kept)
    save_recent_phrase_ids([x for x in load_recent_phrase_ids() if x != pid])
