"""⑩ 個票の形式別エクスポート（PDF / JPEG / PNG）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from PIL import Image

from models.ink_repo import collect_warped_ink_strokes
from models.output_repo import get_feedback_export_format, get_feedback_style
from models.text_annotation_repo import collect_warped_text_annotations
from models.test_repo import get_test_info
from services.feedback_pdf import render_feedback_pdf
from services.feedback_renderer import build_feedback_payload, render_feedback_image

FeedbackExportFormat = Literal["pdf", "jpeg", "png"]

EXPORT_FORMAT_EXTENSIONS: dict[FeedbackExportFormat, str] = {
    "pdf": ".pdf",
    "jpeg": ".jpg",
    "png": ".png",
}


def normalize_export_format(fmt: str | None) -> FeedbackExportFormat:
    value = str(fmt or get_feedback_export_format()).strip().lower()
    if value in EXPORT_FORMAT_EXTENSIONS:
        return value  # type: ignore[return-value]
    return "pdf"


def feedback_filename(student_id: str, student_name: str, fmt: FeedbackExportFormat) -> str:
    sid = _safe_name(student_id or "不明")
    sname = _safe_name(student_name or "")
    ext = EXPORT_FORMAT_EXTENSIONS[fmt]
    return f"個票_{sid}_{sname}{ext}"


def gather_row_render_data(test_id: str, row: dict[str, Any]) -> dict[str, Any]:
    info = get_test_info(test_id)
    points = {k: int(v) for k, v in (info.get("points") or {}).items()}
    payload = build_feedback_payload(test_id, row, points)
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
        "style": get_feedback_style(),
    }


def export_feedback_row(
    test_id: str,
    row: dict[str, Any],
    out_path: str | Path,
    fmt: FeedbackExportFormat | str | None = None,
) -> Path:
    export_fmt = normalize_export_format(fmt)  # type: ignore[assignment]
    data = gather_row_render_data(test_id, row)
    payload = data["payload"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if export_fmt == "pdf":
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

    image = render_feedback_image(
        data["warped_path"],
        payload["fields"],
        payload["outputSlots"],
        payload["fieldMarks"],
        payload["totals"],
        style=data["style"],
        ink_strokes=data["ink_strokes"],
        text_annotations=data["text_annotations"],
    )
    if export_fmt == "png":
        image.save(out_path, "PNG")
    else:
        image.save(out_path, "JPEG", quality=92)
    return out_path


def _safe_name(value: str) -> str:
    return "".join(c for c in str(value or "") if c not in '\\/:*?"<>|').strip() or "無名"
