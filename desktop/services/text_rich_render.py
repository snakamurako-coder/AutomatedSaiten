"""リッチテキスト注釈のラスター／PDF 描画。"""

from __future__ import annotations

from typing import Any

from PIL import Image

from models.text_annotation_repo import resolve_text_style
from ui_qt.floating_palette.text_rich import box_text_html, html_for_pdf_box


def render_annotation_html_to_pil(
    size: tuple[int, int],
    box: dict[str, Any],
    *,
    scale: float = 1.0,
) -> Image.Image | None:
    """1 件のテキストボックス HTML を透過 RGBA 画像にレンダー。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter, QTextDocument
    from PIL.ImageQt import ImageQt

    st = resolve_text_style(box.get("style") or {})
    html = box_text_html(box, st)
    if not html.strip():
        return None

    work_scale = max(0.01, float(scale))
    w = max(20.0, float(box.get("width") or 32) * work_scale)
    h = max(12.0, float(box.get("height") or 18) * work_scale)
    x = float(box.get("x") or 0) * work_scale
    y = float(box.get("y") or 0) * work_scale

    doc = QTextDocument()
    doc.setDefaultStyleSheet("body { margin: 0; padding: 0; }")
    doc.setHtml(html)
    doc.setTextWidth(max(1.0, w))

    canvas_w = max(1, int(size[0]))
    canvas_h = max(1, int(size[1]))
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    doc_h = max(h, doc.size().height())
    qimg = QImage(int(w), int(doc_h), QImage.Format.Format_ARGB32_Premultiplied)
    qimg.fill(Qt.GlobalColor.transparent)
    painter = QPainter(qimg)
    try:
        doc.drawContents(painter)
    finally:
        painter.end()

    patch = ImageQt(qimg).convert("RGBA")
    layer.paste(patch, (int(x), int(y)), patch)
    return layer


def draw_annotation_pdf(page, box: dict[str, Any]) -> None:
    """fitz.Page にテキストボックス 1 件を描画。"""
    import fitz

    from services.feedback_pdf import _hex_to_rgb01, _resolve_font_file

    st = resolve_text_style(box.get("style") or {})
    x = float(box.get("x") or 0)
    y = float(box.get("y") or 0)
    w = max(20.0, float(box.get("width") or 32))
    h = max(12.0, float(box.get("height") or 18))
    rect = fitz.Rect(x, y, x + w, y + h)

    fill_alpha = float(st.get("fillAlpha") or 0)
    if fill_alpha > 0:
        fc = st.get("fillColor") or "#ffffff"
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(
            color=_hex_to_rgb01(fc),
            fill=_hex_to_rgb01(fc),
            fill_opacity=fill_alpha,
            width=0,
            closePath=True,
        )
        shape.commit()

    html = html_for_pdf_box(box_text_html(box, st), st)
    if not html.strip():
        return
    try:
        page.insert_htmlbox(rect, html)
    except Exception:
        text = str(box.get("text") or "").strip()
        if not text:
            return
        font_path = _resolve_font_file(preferred=str(st.get("fontFamily") or "") or None)
        fs = max(8.0, float(st.get("fontSize") or 14))
        page.insert_textbox(
            rect,
            text,
            fontfile=font_path,
            fontsize=fs,
            color=_hex_to_rgb01(st.get("textColor") or "#111827"),
            align=fitz.TEXT_ALIGN_LEFT,
        )
