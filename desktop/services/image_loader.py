"""画像・PDF の読み込み（JPG / PNG / PDF 1ページ目）。

Windows では cv2.imread / imwrite が日本語パスを扱えないため、
バイト読み書き + imdecode / imencode を使う。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# 必須サポート形式
PRIMARY_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
PDF_EXTENSIONS = {".pdf"}
# 追加の画像形式（OpenCV / Pillow）
EXTRA_IMAGE_EXTENSIONS = {".bmp", ".webp", ".tif", ".tiff"}
ALL_INPUT_EXTENSIONS = PRIMARY_EXTENSIONS | EXTRA_IMAGE_EXTENSIONS

FILE_DIALOG_PATTERNS = "*.pdf *.jpg *.jpeg *.png"


def is_supported_input_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ALL_INPUT_EXTENSIONS


def is_pdf_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PDF_EXTENSIONS


def imread_bgr(path: str | Path) -> np.ndarray | None:
    """Unicode パス対応の画像読み込み（BGR）。失敗時は None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return image


def imwrite_bgr(path: str | Path, image_bgr: np.ndarray, *, quality: int = 90) -> None:
    """Unicode パス対応の画像保存（BGR）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower() or ".jpg"
    if ext in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        ok, buf = cv2.imencode(".jpg", image_bgr, params)
    elif ext == ".png":
        ok, buf = cv2.imencode(".png", image_bgr)
    else:
        ok, buf = cv2.imencode(ext, image_bgr)
    if not ok:
        raise ValueError(f"画像のエンコードに失敗しました: {path}")
    buf.tofile(str(path))


def load_image_bgr(path: str | Path, *, pdf_page: int = 0) -> np.ndarray:
    """ファイルを BGR の numpy 配列として読み込む。PDF は指定ページ（0始まり）。"""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"ファイルが見つかりません: {path}")
    ext = path.suffix.lower()
    if ext not in ALL_INPUT_EXTENSIONS:
        raise ValueError(
            f"未対応の形式です: {ext}（対応: PDF, JPG, PNG ほか bmp/webp/tiff）"
        )
    if ext in PDF_EXTENSIONS:
        return _load_pdf_page_bgr(path, pdf_page)

    image = imread_bgr(path)
    if image is not None:
        return image

    try:
        from PIL import Image

        with Image.open(path) as pil:
            rgb = np.array(pil.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as e:
        raise ValueError(f"画像を読み込めません: {path}") from e


def _load_pdf_page_bgr(path: Path, page_index: int) -> np.ndarray:
    try:
        import fitz
    except ImportError as e:
        raise ValueError(
            "PDF を読み込むには pymupdf が必要です。"
            " desktop フォルダで py -3 -m pip install -r requirements.txt を実行してください。"
        ) from e

    doc = fitz.open(path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(
                f"PDF にページ {page_index + 1} がありません（全 {doc.page_count} ページ）"
            )
        page = doc[page_index]
        # GAS 版 pdf.js の scale: 2.0 に合わせる
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        if pix.n == 3:
            return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        if pix.n == 1:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        raise ValueError(f"PDF ページの色形式が未対応です: n={pix.n}")
    finally:
        doc.close()
