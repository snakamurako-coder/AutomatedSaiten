"""⑩ 個票の形式別エクスポート（PDF / JPEG / PNG）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import fitz
from PIL import Image

from models.ink_repo import collect_warped_ink_strokes
from models.output_repo import (
    FEEDBACK_IMAGE_BASIS_ORIGINAL,
    get_feedback_export_format,
    get_feedback_style,
    normalize_feedback_image_basis,
)
from models.text_annotation_repo import collect_warped_text_annotations
from models.test_repo import get_test_info
from services.feedback_pdf import (
    build_feedback_pdf_document,
    build_pdf_document_from_image,
    pdf_document_to_bytes,
    rasterize_pdf_bytes,
    render_feedback_pdf,
)
from services.feedback_renderer import (
    build_feedback_payload,
    build_feedback_shared_context,
    render_feedback_image,
)
FeedbackExportFormat = Literal["pdf", "pdf_combined", "jpeg", "png"]
PerFileExportFormat = Literal["pdf", "jpeg", "png"]

COMBINED_PDF_FILENAME = "個票_一括.pdf"

EXPORT_FORMAT_EXTENSIONS: dict[PerFileExportFormat, str] = {
    "pdf": ".pdf",
    "jpeg": ".jpg",
    "png": ".png",
}


def normalize_export_format(fmt: str | None) -> FeedbackExportFormat:
    value = str(fmt or get_feedback_export_format()).strip().lower()
    if value in ("pdf", "pdf_combined", "jpeg", "png"):
        return value  # type: ignore[return-value]
    return "pdf"


def is_pdf_export_format(fmt: str | None) -> bool:
    return normalize_export_format(fmt) in ("pdf", "pdf_combined")


def is_combined_pdf_export(fmt: str | None) -> bool:
    return normalize_export_format(fmt) == "pdf_combined"


def per_file_export_format(fmt: str | None) -> PerFileExportFormat:
    normalized = normalize_export_format(fmt)
    if normalized == "pdf_combined":
        return "pdf"
    return normalized  # type: ignore[return-value]


def feedback_filename(student_id: str, student_name: str, fmt: str | None) -> str:
    file_fmt = per_file_export_format(fmt)
    sid = _safe_name(student_id or "不明")
    sname = _safe_name(student_name or "")
    ext = EXPORT_FORMAT_EXTENSIONS[file_fmt]
    return f"個票_{sid}_{sname}{ext}"


def sheet_source_ready(
    row: dict[str, Any],
    *,
    test_id: str,
    image_basis: str | None = None,
) -> bool:
    """個票の下地に必要な画像があるか。元画像出力でも判定位置のため補正画像が要る。"""
    warped = str(row.get("warpedPath") or "").strip()
    if not warped or not Path(warped).exists():
        return False
    if normalize_feedback_image_basis(image_basis) != FEEDBACK_IMAGE_BASIS_ORIGINAL:
        return True
    from services.crop_preview import resolve_source_path

    try:
        resolve_source_path(row, test_id=test_id)
    except FileNotFoundError:
        return False
    return True


def gather_row_render_data(
    test_id: str,
    row: dict[str, Any],
    *,
    shared: dict[str, Any] | None = None,
    output_slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if shared is None:
        info = get_test_info(test_id)
        points = {k: int(v) for k, v in (info.get("points") or {}).items()}
        payload = build_feedback_payload(
            test_id, row, points, output_slots=output_slots
        )
        style = get_feedback_style()
    else:
        points = shared["points"]
        slots = output_slots if output_slots is not None else shared["output_slots"]
        payload = build_feedback_payload(
            test_id,
            row,
            points,
            fields=shared["fields"],
            output_slots=slots,
            domain_settings=shared.get("domain_settings"),
        )
        style = shared["style"]
    warped = str(row.get("warpedPath") or "").strip()
    if not warped or not Path(warped).exists():
        raise FileNotFoundError(f"補正画像が見つかりません: {row.get('fileName')}")
    result_id = int(row.get("id") or 0)
    ink = collect_warped_ink_strokes(test_id, result_id, payload["fields"]) if result_id else []
    text_ann = (
        collect_warped_text_annotations(test_id, result_id, payload["fields"])
        if result_id
        else []
    )
    return {
        "warped_path": warped,
        "payload": payload,
        "ink_strokes": ink,
        "text_annotations": text_ann,
        "style": style,
    }


def build_row_pdf_document(
    test_id: str,
    row: dict[str, Any],
    *,
    shared: dict[str, Any] | None = None,
    image_basis: str | None = None,
) -> fitz.Document:
    """1生徒分の個票 PDF ドキュメント（表裏一体印刷などから利用）。"""
    return _build_row_pdf_document(
        test_id, row, shared=shared, image_basis=image_basis
    )


def _compose_row_image(
    test_id: str,
    row: dict[str, Any],
    data: dict[str, Any],
    *,
    image_basis: str | None,
) -> Image.Image:
    payload = data["payload"]
    if normalize_feedback_image_basis(image_basis) == FEEDBACK_IMAGE_BASIS_ORIGINAL:
        from services.original_feedback import render_feedback_on_original

        return render_feedback_on_original(
            test_id,
            row,
            data["warped_path"],
            payload["fields"],
            payload["outputSlots"],
            payload["fieldMarks"],
            payload["totals"],
            data["style"],
            ink_strokes=data["ink_strokes"],
            text_annotations=data["text_annotations"],
        )
    return render_feedback_image(
        data["warped_path"],
        payload["fields"],
        payload["outputSlots"],
        payload["fieldMarks"],
        payload["totals"],
        style=data["style"],
        ink_strokes=data["ink_strokes"],
        text_annotations=data["text_annotations"],
    )


def _build_row_pdf_document(
    test_id: str,
    row: dict[str, Any],
    *,
    shared: dict[str, Any] | None = None,
    output_slots: list[dict[str, Any]] | None = None,
    image_basis: str | None = None,
) -> fitz.Document:
    data = gather_row_render_data(
        test_id, row, shared=shared, output_slots=output_slots
    )
    payload = data["payload"]
    if normalize_feedback_image_basis(image_basis) == FEEDBACK_IMAGE_BASIS_ORIGINAL:
        image = _compose_row_image(test_id, row, data, image_basis=image_basis)
        return build_pdf_document_from_image(image)
    return build_feedback_pdf_document(
        data["warped_path"],
        payload["fields"],
        payload["outputSlots"],
        payload["fieldMarks"],
        payload["totals"],
        data["style"],
        ink_strokes=data["ink_strokes"],
        text_annotations=data["text_annotations"],
    )


def export_feedback_row(
    test_id: str,
    row: dict[str, Any],
    out_path: str | Path,
    fmt: FeedbackExportFormat | str | None = None,
    *,
    shared: dict[str, Any] | None = None,
    image_basis: str | None = None,
) -> Path:
    export_fmt = per_file_export_format(fmt)
    data = gather_row_render_data(test_id, row, shared=shared)
    payload = data["payload"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    basis = normalize_feedback_image_basis(image_basis)

    if export_fmt == "pdf" and basis != FEEDBACK_IMAGE_BASIS_ORIGINAL:
        return render_feedback_pdf(
            data["warped_path"],
            payload["fields"],
            payload["outputSlots"],
            payload["fieldMarks"],
            payload["totals"],
            data["style"],
            ink_strokes=data["ink_strokes"],
            text_annotations=data["text_annotations"],
            out_path=out_path,
        )

    image = _compose_row_image(test_id, row, data, image_basis=basis)
    if export_fmt == "pdf":
        doc = build_pdf_document_from_image(image)
        try:
            doc.save(str(out_path))
        finally:
            doc.close()
        return out_path
    if export_fmt == "png":
        image.save(out_path, "PNG")
    else:
        image.save(out_path, "JPEG", quality=92)
    return out_path


def export_combined_feedback_pdf(
    test_id: str,
    rows: list[dict[str, Any]],
    out_path: str | Path,
    on_progress: Callable[[int, int, str], None] | None = None,
    *,
    shared: dict[str, Any] | None = None,
    image_basis: str | None = None,
) -> tuple[int, list[str], list[dict[str, str]]]:
    """全対象行を 1 つの PDF にまとめて保存する。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ctx = shared if shared is not None else build_feedback_shared_context(test_id)
    master = fitz.open()
    saved = 0
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    total = len(rows)
    try:
        for i, row in enumerate(rows):
            name = str(row.get("fileName") or "")
            if on_progress:
                on_progress(i + 1, total, name)
            if not sheet_source_ready(row, test_id=test_id, image_basis=image_basis):
                skipped.append(name)
                continue
            try:
                doc = _build_row_pdf_document(
                    test_id, row, shared=ctx, image_basis=image_basis
                )
                try:
                    master.insert_pdf(doc)
                    saved += 1
                finally:
                    doc.close()
            except Exception as exc:
                errors.append({"fileName": name, "error": str(exc)})
        if saved <= 0:
            missing = (
                "元画像または補正画像のある行がありません"
                if normalize_feedback_image_basis(image_basis) == FEEDBACK_IMAGE_BASIS_ORIGINAL
                else "補正画像のある行がありません"
            )
            raise ValueError(f"出力可能な個票がありません（{missing}）。")
        master.save(str(out_path))
    finally:
        master.close()
    return saved, skipped, errors


def _safe_name(value: str) -> str:
    return "".join(c for c in str(value or "") if c not in '\\/:*?"<>|').strip() or "無名"


def render_feedback_preview(
    test_id: str,
    row: dict[str, Any],
    fmt: FeedbackExportFormat | str | None = None,
    *,
    output_slots: list[dict[str, Any]] | None = None,
    image_basis: str | None = None,
) -> dict[str, Any]:
    """1 件プレビュー用。PDF 形式時はベクトル PDF を生成し、表示用に高解像度ラスター化する。

    output_slots を渡すと、保存済みではなくその配置（総計点など）を描く。
    """
    basis = normalize_feedback_image_basis(image_basis)
    if is_pdf_export_format(fmt) and basis != FEEDBACK_IMAGE_BASIS_ORIGINAL:
        doc = _build_row_pdf_document(
            test_id, row, output_slots=output_slots, image_basis=basis
        )
        try:
            page = doc[0]
            native_size = (int(round(page.rect.width)), int(round(page.rect.height)))
            pdf_bytes = pdf_document_to_bytes(doc)
        finally:
            doc.close()
        return {
            "mode": "pdf",
            "pdf_bytes": pdf_bytes,
            "native_size": native_size,
            "image": rasterize_pdf_bytes(pdf_bytes, scale=2.0),
        }

    export_fmt = per_file_export_format(fmt)
    data = gather_row_render_data(test_id, row, output_slots=output_slots)
    image = _compose_row_image(test_id, row, data, image_basis=basis)
    return {
        "mode": "raster",
        "pdf_bytes": None,
        "native_size": image.size,
        "image": image,
        "raster_format": export_fmt,
    }


def rasterize_feedback_preview(
    preview: dict[str, Any],
    *,
    zoom_pct: float,
) -> Image.Image:
    """プレビュー表示倍率に応じて PDF を再ラスター化する（ズーム時もベクトルの鮮明さを維持）。"""
    zoom = max(0.1, float(zoom_pct) / 100.0)
    if preview.get("mode") == "pdf" and preview.get("pdf_bytes"):
        scale = max(2.0, zoom * 2.0)
        return rasterize_pdf_bytes(preview["pdf_bytes"], scale=scale)
    return preview["image"]
