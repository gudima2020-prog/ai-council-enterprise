from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import unicodedata

import pypdfium2 as pdfium


_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_]+$")


class RunnerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "value must be positive"
        )
    return parsed


def psm_value(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 13:
        raise argparse.ArgumentTypeError(
            "PSM must be between 0 and 13"
        )
    return parsed


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Studio isolated PDF OCR runner."
    )
    parser.add_argument(
        "--input",
        required=True,
    )
    parser.add_argument(
        "--pages",
        default="",
    )
    parser.add_argument(
        "--dpi",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-pdf-pages",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-selected-pages",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-page-pixels",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-total-pixels",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-png-bytes",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-page-characters",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--max-total-characters",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--page-timeout",
        type=positive_int,
        required=True,
    )
    parser.add_argument(
        "--languages",
        required=True,
    )
    parser.add_argument(
        "--psm",
        type=psm_value,
        required=True,
    )
    return parser.parse_args()


def safe_languages(value: str) -> tuple[str, ...]:
    items = tuple(
        item
        for item in value.split("+")
        if item
    )
    if not items:
        raise RunnerError(
            "DOCUMENT_OCR_LANGUAGES_INVALID",
            "OCR languages are invalid.",
        )
    if any(
        not _LANGUAGE_RE.fullmatch(item)
        for item in items
    ):
        raise RunnerError(
            "DOCUMENT_OCR_LANGUAGES_INVALID",
            "OCR languages are invalid.",
        )
    return items


def selected_pages(
    *,
    raw: str,
    page_count: int,
    maximum: int,
) -> tuple[int, ...]:
    if raw.strip():
        try:
            selected = tuple(
                sorted(
                    {
                        int(item)
                        for item in raw.split(",")
                        if item
                    }
                )
            )
        except ValueError as exc:
            raise RunnerError(
                "DOCUMENT_OCR_PAGE_SELECTION_INVALID",
                "OCR page selection is invalid.",
            ) from exc
    else:
        selected = tuple(
            range(1, page_count + 1)
        )

    if not selected:
        raise RunnerError(
            "DOCUMENT_OCR_PAGE_SELECTION_EMPTY",
            "At least one OCR page must be selected.",
        )
    if len(selected) > maximum:
        raise RunnerError(
            "DOCUMENT_OCR_SELECTED_PAGE_LIMIT",
            "Selected OCR page count exceeds the configured limit.",
            details={
                "selected_page_count": len(selected)
            },
        )
    invalid = [
        page_number
        for page_number in selected
        if (
            page_number < 1
            or page_number > page_count
        )
    ]
    if invalid:
        raise RunnerError(
            "DOCUMENT_OCR_PAGE_OUT_OF_RANGE",
            "OCR page selection is outside the PDF range.",
            details={"page_number": invalid[0]},
        )
    return selected


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKC",
        value,
    )
    normalized = normalized.replace(
        "\r\n",
        "\n",
    ).replace("\r", "\n")
    lines = [
        line.rstrip()
        for line in normalized.split("\n")
    ]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def tesseract_version() -> str:
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            shell=False,
        )
    except Exception:
        return "5"
    first = (
        (result.stdout or "")
        .splitlines()[0:1]
    )
    if not first:
        return "5"
    return first[0][:128]


def process(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        raise RunnerError(
            "DOCUMENT_OCR_SOURCE_MISSING",
            "OCR source PDF is missing.",
        )

    languages = safe_languages(args.languages)
    try:
        document = pdfium.PdfDocument(str(input_path))
    except Exception as exc:
        raise RunnerError(
            "DOCUMENT_OCR_PDF_INVALID",
            "PDF cannot be opened by the isolated renderer.",
        ) from exc

    pages: list[dict[str, Any]] = []
    total_pixels = 0
    total_characters = 0
    warnings: list[str] = []

    try:
        page_count = len(document)
        if page_count <= 0:
            raise RunnerError(
                "DOCUMENT_OCR_PDF_EMPTY",
                "PDF has no pages.",
            )
        if page_count > args.max_pdf_pages:
            raise RunnerError(
                "DOCUMENT_OCR_PDF_PAGE_LIMIT",
                "PDF page count exceeds the configured OCR limit.",
                details={"page_count": page_count},
            )
        selected = selected_pages(
            raw=args.pages,
            page_count=page_count,
            maximum=args.max_selected_pages,
        )

        scale = args.dpi / 72
        for page_number in selected:
            page = document[page_number - 1]
            try:
                width_points, height_points = (
                    page.get_size()
                )
                width_pixels = max(
                    1,
                    math.ceil(
                        width_points * args.dpi / 72
                    ),
                )
                height_pixels = max(
                    1,
                    math.ceil(
                        height_points * args.dpi / 72
                    ),
                )
                pixel_count = (
                    width_pixels * height_pixels
                )
                if pixel_count > args.max_page_pixels:
                    raise RunnerError(
                        "DOCUMENT_OCR_PAGE_PIXEL_LIMIT",
                        "Rendered OCR page exceeds the pixel limit.",
                        details={
                            "page_number": page_number,
                            "pixel_count": pixel_count,
                        },
                    )
                total_pixels += pixel_count
                if total_pixels > args.max_total_pixels:
                    raise RunnerError(
                        "DOCUMENT_OCR_TOTAL_PIXEL_LIMIT",
                        "OCR render workload exceeds the total pixel limit.",
                        details={
                            "total_pixels": total_pixels
                        },
                    )

                image_path = Path(
                    f"/tmp/page-{page_number}.png"
                )
                bitmap = page.render(
                    scale=scale,
                    rotation=0,
                )
                try:
                    image = bitmap.to_pil()
                    try:
                        image.save(
                            image_path,
                            format="PNG",
                            optimize=False,
                            compress_level=6,
                        )
                    finally:
                        image.close()
                finally:
                    bitmap.close()

                png_size = image_path.stat().st_size
                if png_size > args.max_png_bytes:
                    raise RunnerError(
                        "DOCUMENT_OCR_PAGE_IMAGE_LIMIT",
                        "Rendered OCR page image exceeds the byte limit.",
                        details={
                            "page_number": page_number,
                            "png_size_bytes": png_size,
                        },
                    )
                png_hash = file_sha256(image_path)

                try:
                    recognized = subprocess.run(
                        [
                            "tesseract",
                            str(image_path),
                            "stdout",
                            "-l",
                            "+".join(languages),
                            "--psm",
                            str(args.psm),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=args.page_timeout,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RunnerError(
                        "DOCUMENT_OCR_PAGE_TIMEOUT",
                        "OCR page processing exceeded the configured timeout.",
                        details={
                            "page_number": page_number,
                            "timeout_seconds": (
                                args.page_timeout
                            ),
                        },
                    ) from exc
                finally:
                    image_path.unlink(
                        missing_ok=True
                    )

                if recognized.returncode != 0:
                    raise RunnerError(
                        "DOCUMENT_OCR_ENGINE_FAILED",
                        "OCR engine failed to process the page.",
                        details={
                            "page_number": page_number,
                            "returncode": (
                                recognized.returncode
                            ),
                        },
                    )

                text = normalize_text(
                    recognized.stdout or ""
                )
                if (
                    len(text)
                    > args.max_page_characters
                ):
                    raise RunnerError(
                        "DOCUMENT_OCR_PAGE_TEXT_LIMIT",
                        "OCR page text exceeds the configured limit.",
                        details={
                            "page_number": page_number,
                            "characters": len(text),
                        },
                    )
                total_characters += len(text)
                if (
                    total_characters
                    > args.max_total_characters
                ):
                    raise RunnerError(
                        "DOCUMENT_OCR_TOTAL_TEXT_LIMIT",
                        "OCR text exceeds the configured total limit.",
                        details={
                            "characters": total_characters
                        },
                    )

                page_warnings: list[str] = []
                if (recognized.stderr or "").strip():
                    page_warnings.append(
                        "OCR_ENGINE_STDERR"
                    )
                if not text:
                    page_warnings.append(
                        "OCR_PAGE_NO_TEXT"
                    )
                    warnings.append(
                        f"OCR_PAGE_{page_number}_NO_TEXT"
                    )

                pages.append(
                    {
                        "page_number": page_number,
                        "text": text,
                        "width_pixels": width_pixels,
                        "height_pixels": height_pixels,
                        "pixel_count": pixel_count,
                        "png_sha256": png_hash,
                        "png_size_bytes": png_size,
                        "warnings": page_warnings,
                    }
                )
            finally:
                page.close()
    finally:
        document.close()

    return {
        "ok": True,
        "page_count": page_count,
        "selected_page_count": len(pages),
        "pages": pages,
        "engine": "tesseract",
        "engine_version": tesseract_version(),
        "renderer": "pdfium",
        "renderer_version": str(
            getattr(
                pdfium,
                "PYPDFIUM_INFO",
                "unknown",
            )
        ),
        "warnings": warnings,
    }


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    sys.stdout.flush()


def main() -> int:
    try:
        payload = process(parse_arguments())
    except RunnerError as exc:
        emit(
            {
                "ok": False,
                "error_code": exc.code,
                "message": str(exc),
                "details": exc.details,
            }
        )
        return 2
    except Exception:
        emit(
            {
                "ok": False,
                "error_code": (
                    "DOCUMENT_OCR_RUNTIME_INTERNAL_ERROR"
                ),
                "message": (
                    "Isolated OCR runtime failed unexpectedly."
                ),
                "details": {},
            }
        )
        return 3

    emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
