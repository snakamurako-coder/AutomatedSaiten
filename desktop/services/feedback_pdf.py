"""⑩ 個票のベクトル PDF 出力（手書き・テキスト・判定マークをベクトル描画）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import fitz

from services.compositor import hex_to_rgba
from services.feedback_renderer import (
    _inset_rect,
    format_total_text,
    normalize_judgment,
)
from services.image_loader import imread_bgr

_FONT_CANDIDATES = ["meiryo.ttc", "meiryb.ttc", "YuGothM.ttc", "msgothic.ttc", "arial.ttf"]
_FONT_CANDIDATES_BOLD = ["meiryb.ttc", "meiryo.ttc", "YuGothB.ttc", "msgothic.ttc", "arialbd.ttf"]


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _a = hex_to_rgba(hex_color, 1.0)
    return r / 255.0, g / 255.0, b / 255.0


def _resolve_font_file(*, bold: bool = False, preferred: str | None = None) -> str:
    names: list[str] = []
    if preferred:
        names.append(str(preferred))
    names.extend(_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES)
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        direct = Path(name)
        if direct.is_file():
            return str(direct)
        cand = fonts_dir / name
        if cand.is_file():
            return str(cand)
    raise FileNotFoundError("日本語フォントが見つかりません（meiryo.ttc 等）")


def _fit_font_size(
    text: str,
    font_path: str,
    max_size: float,
    max_width: float,
    *,
    min_size: float = 8.0,
) -> float:
    font = fitz.Font(fontfile=font_path)
    size = max(min_size, float(max_size))
    while size > min_size:
        if font.text_length(text, fontsize=size) <= max_width:
            return size
        size = max(min_size, size * 0.9)
    return size


def _draw_centered_textbox(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    color: tuple[float, float, float],
    font_size: float,
    *,
    bold: bool = False,
    font_path: str | None = None,
) -> None:
    if not text:
        return
    path = font_path or _resolve_font_file(bold=bold)
    fs = _fit_font_size(text, path, font_size, rect.width * 0.92, min_size=8.0)
    page.insert_textbox(
        rect,
        text,
        fontfile=path,
        fontsize=fs,
        color=color,
        align=fitz.TEXT_ALIGN_CENTER,
    )


def _draw_mark_pdf(
    page: fitz.Page,
    x: float,
    y: float,
    w: float,
    h: float,
    judgment: str,
    score: Any,
    style: dict[str, Any],
) -> None:
    kind = normalize_judgment(judgment, score)
    if kind is None:
        return
    mark_style = style["mark"]
    ix, iy, iw, ih = _inset_rect(x, y, w, h, float(mark_style.get("insetRatio", 0.05)))
    min_dim = min(iw, ih)
    rect = fitz.Rect(ix, iy, ix + iw, iy + ih)
    shape = page.new_shape()

    if kind == "maru":
        st = mark_style["maru"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        fill = _hex_to_rgb01(st["strokeColor"])
        stroke = _hex_to_rgb01(st["strokeColor"])
        shape.draw_oval(rect)
        shape.finish(
            width=line_w,
            color=stroke,
            fill=fill,
            fill_opacity=float(st.get("fillOpacity", 0.12)),
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            closePath=False,
        )
        shape.commit()
    elif kind == "sankaku":
        st = mark_style["sankaku"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        color = _hex_to_rgb01(st["strokeColor"])
        points = [
            fitz.Point(ix + iw / 2, iy),
            fitz.Point(ix + iw, iy + ih),
            fitz.Point(ix, iy + ih),
            fitz.Point(ix + iw / 2, iy),
        ]
        shape.draw_polyline(points)
        shape.finish(
            width=line_w,
            color=color,
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            closePath=False,
        )
        shape.commit()
    else:
        st = mark_style["batsu"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.08)))
        color = _hex_to_rgb01(st["strokeColor"])
        for p1, p2 in (
            (fitz.Point(ix, iy), fitz.Point(ix + iw, iy + ih)),
            (fitz.Point(ix + iw, iy), fitz.Point(ix, iy + ih)),
        ):
            seg = page.new_shape()
            seg.draw_line(p1, p2)
            seg.finish(
                width=line_w,
                color=color,
                stroke_opacity=float(st.get("strokeOpacity", 1.0)),
                closePath=False,
            )
            seg.commit()

    score_text = "" if score is None else str(score).strip()
    if not score_text:
        return
    try:
        if kind == "batsu" and float(score_text) == 0:
            return
    except ValueError:
        pass
    sc = mark_style["score"]
    _draw_centered_textbox(
        page,
        rect,
        score_text,
        _hex_to_rgb01(sc["color"]),
        min_dim * float(sc.get("sizeRatio", 0.35)),
        bold=True,
    )


def _draw_total_pdf(
    page: fitz.Page,
    slot: dict[str, Any],
    value: Any,
    style: dict[str, Any],
) -> None:
    if value is None or str(value) == "":
        return
    st = style["total"]
    x, y = float(slot["x"]), float(slot["y"])
    w, h = float(slot["width"]), float(slot["height"])
    font_size = max(
        float(st.get("minFontSize", 10)), min(w, h) * float(st.get("sizeRatio", 0.5))
    )
    _draw_centered_textbox(
        page,
        fitz.Rect(x, y, x + w, y + h),
        format_total_text(slot, value),
        _hex_to_rgb01(st["color"]),
        font_size,
        bold=True,
    )


def _draw_text_annotations_pdf(
    page: fitz.Page,
    annotations: list[dict[str, Any]],
) -> None:
    from services.text_rich_render import draw_annotation_pdf

    for box in annotations or []:
        draw_annotation_pdf(page, box)


def _draw_ink_strokes_pdf(
    page: fitz.Page,
    strokes: list[dict[str, Any]],
    *,
    page_size: tuple[int, int],
) -> None:
    """手書きはネイティブ解像度で1枚ラスタ化し PDF に重ねる（線分 Shape 爆発を避ける）。"""
    from io import BytesIO

    from services.compositor import render_ink_layer

    if not strokes:
        return
    w, h = int(page_size[0]), int(page_size[1])
    if w <= 0 or h <= 0:
        return
    layer = render_ink_layer((w, h), strokes, scale=1.0, supersample=1)
    # 完全透明ならスキップ
    extrema = layer.getextrema()
    if extrema and len(extrema) >= 4 and extrema[3][1] == 0:
        return
    buf = BytesIO()
    layer.save(buf, format="PNG")
    page.insert_image(fitz.Rect(0, 0, w, h), stream=buf.getvalue())


def build_feedback_pdf_document(
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
    jpeg_quality: int = 92,
) -> fitz.Document:
    """個票 PDF をメモリ上に構築する。呼び出し側で close() すること。"""
    bgr = imread_bgr(warped_path)
    if bgr is None:
        raise ValueError(f"補正画像を読み込めません: {warped_path}")

    h_px, w_px = bgr.shape[:2]
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise ValueError("補正画像の JPEG エンコードに失敗しました")

    doc = fitz.open()
    page = doc.new_page(width=w_px, height=h_px)
    page.insert_image(fitz.Rect(0, 0, w_px, h_px), stream=bytes(buf))

    for f in fields:
        marks = field_marks.get(f["id"]) or field_marks.get(f.get("displayName") or "") or {}
        _draw_mark_pdf(
            page,
            float(f["x"]),
            float(f["y"]),
            float(f["width"]),
            float(f["height"]),
            str(marks.get("judgment") or ""),
            marks.get("score"),
            style,
        )
    for slot in output_slots:
        _draw_total_pdf(page, slot, totals.get(slot["slotKey"]), style)
    if text_annotations:
        _draw_text_annotations_pdf(page, text_annotations)
    if ink_strokes:
        _draw_ink_strokes_pdf(page, ink_strokes, page_size=(w_px, h_px))
    return doc


def rasterize_pdf_bytes(pdf_bytes: bytes, *, scale: float = 2.0) -> Image.Image:
    """ベクトル PDF をプレビュー用にラスター化する（拡大しても線・文字が滑らか）。"""
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        matrix = fitz.Matrix(float(scale), float(scale))
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def pdf_document_to_bytes(doc: fitz.Document) -> bytes:
    return doc.tobytes(deflate=True, garbage=3)


def render_feedback_pdf(
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
    out_path: str | Path,
    jpeg_quality: int = 92,
) -> Path:
    """補正画像を背景ラスター、上物をベクトルとして PDF に書き出す。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = build_feedback_pdf_document(
        warped_path,
        fields,
        output_slots,
        field_marks,
        totals,
        style,
        ink_strokes=ink_strokes,
        text_annotations=text_annotations,
        jpeg_quality=jpeg_quality,
    )
    try:
        doc.save(str(out_path))
    finally:
        doc.close()
    return out_path
