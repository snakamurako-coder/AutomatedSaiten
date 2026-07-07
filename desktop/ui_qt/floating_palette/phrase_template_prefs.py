"""定型文（フレーズシール）テンプレートの永続化（config.json）。"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from config import load_config, save_config
from models.text_annotation_repo import TEXT_STYLE_TEMPLATE_A, resolve_text_style

PHRASE_SIMPLE_COUNT = 6

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
    label = str(raw.get("label") or "").strip() or text.replace("\n", " ")[:20] or "定型文"
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


def phrase_from_text_box(box: dict[str, Any]) -> dict[str, Any]:
    plain = str(box.get("text") or "").strip()
    label = plain.replace("\n", " ")[:20] or "定型文"
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


def delete_phrase_template(phrase_id: str) -> None:
    pid = str(phrase_id or "").strip()
    if not pid:
        return
    templates = [t for t in load_phrase_templates() if str(t.get("id")) != pid]
    save_phrase_templates(templates)
    save_recent_phrase_ids([x for x in load_recent_phrase_ids() if x != pid])
