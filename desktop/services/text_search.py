"""テスト横断の OCR／テキストボックス文字列検索。"""

from __future__ import annotations

from typing import Any, Literal

from models.ink_repo import SHEET_FIELD_ID, is_sheet_field_id
from models.text_annotation_repo import iter_text_annotations_for_test
from models.test_repo import get_all_results, get_answer_fields

HitKind = Literal["ocr", "textbox"]


def _normalize_query(query: str) -> str:
    return str(query or "").strip().casefold()


def _snippet(text: str, query: str, *, radius: int = 36) -> str:
    raw = str(text or "").replace("\n", " ").strip()
    if not raw:
        return ""
    q = _normalize_query(query)
    lower = raw.casefold()
    idx = lower.find(q) if q else 0
    if idx < 0:
        idx = 0
    start = max(0, idx - radius)
    end = min(len(raw), idx + max(len(query), 1) + radius)
    out = raw[start:end]
    if start > 0:
        out = "…" + out
    if end < len(raw):
        out = out + "…"
    return out


def _box_search_text(box: dict[str, Any]) -> str:
    """TB の検索用プレーンテキスト（HTML タグ除去）。"""
    plain = str(box.get("text") or "")
    html = str(box.get("textHtml") or "")
    if html.strip():
        try:
            from ui_qt.floating_palette.text_rich import _strip_html_tags

            stripped = _strip_html_tags(html).replace("\xa0", " ").strip()
            if stripped:
                return stripped
        except Exception:
            pass
    return plain


def search_test_texts(test_id: str, query: str) -> list[dict[str, Any]]:
    """アクティブテスト内の OCR・TB を部分一致検索する。

    戻り値の各 hit:
      kind, resultId, fieldId, fieldLabel, studentId, studentName, fileName,
      warpedPath, matchedText, snippet, boxId (TB のみ)
    """
    tid = str(test_id or "").strip()
    q = _normalize_query(query)
    if not tid or not q:
        return []

    fields = get_answer_fields(tid)
    field_names = {
        str(f["id"]): str(f.get("displayName") or f["id"]) for f in fields
    }
    field_names[SHEET_FIELD_ID] = "答案全体"

    results = get_all_results(tid)
    by_id = {int(r["id"]): r for r in results}
    hits: list[dict[str, Any]] = []

    for row in results:
        rid = int(row["id"])
        mapping = row.get("textMapping") or {}
        if not isinstance(mapping, dict):
            continue
        for fid, raw in mapping.items():
            text = str(raw or "")
            if not text or q not in text.casefold():
                continue
            fid_s = str(fid)
            hits.append(
                {
                    "kind": "ocr",
                    "resultId": rid,
                    "fieldId": fid_s,
                    "fieldLabel": field_names.get(fid_s, fid_s),
                    "studentId": str(row.get("studentId") or "") or "—",
                    "studentName": str(row.get("name") or ""),
                    "fileName": str(row.get("fileName") or ""),
                    "warpedPath": str(row.get("warpedPath") or ""),
                    "sourcePath": str(row.get("sourcePath") or ""),
                    "matchedText": text,
                    "snippet": _snippet(text, query),
                    "boxId": "",
                }
            )

    for result_id, field_id, boxes in iter_text_annotations_for_test(tid):
        row = by_id.get(int(result_id))
        if row is None:
            continue
        fid_s = str(field_id)
        for box in boxes or []:
            text = _box_search_text(box)
            if not text or q not in text.casefold():
                continue
            hits.append(
                {
                    "kind": "textbox",
                    "resultId": int(result_id),
                    "fieldId": fid_s,
                    "fieldLabel": field_names.get(fid_s, fid_s),
                    "studentId": str(row.get("studentId") or "") or "—",
                    "studentName": str(row.get("name") or ""),
                    "fileName": str(row.get("fileName") or ""),
                    "warpedPath": str(row.get("warpedPath") or ""),
                    "sourcePath": str(row.get("sourcePath") or ""),
                    "matchedText": text,
                    "snippet": _snippet(text, query),
                    "boxId": str(box.get("id") or ""),
                }
            )

    hits.sort(
        key=lambda h: (
            str(h.get("studentId") or ""),
            str(h.get("fileName") or ""),
            0 if h.get("kind") == "ocr" else 1,
            str(h.get("fieldLabel") or ""),
            str(h.get("snippet") or ""),
        )
    )
    return hits


def crop_field_id_for_hit(hit: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    """記述欄画像表示用の field_id（シートTBは先頭の記述欄にフォールバック）。"""
    fid = str(hit.get("fieldId") or "").strip()
    if fid and not is_sheet_field_id(fid):
        return fid
    if fields:
        return str(fields[0].get("id") or "")
    return ""
