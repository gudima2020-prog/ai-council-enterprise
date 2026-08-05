from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from backend.documents.intake import (
    DocumentFormat,
    DocumentIntakeDescriptor,
)
from backend.documents.ocr import (
    DockerTesseractOCRRuntime,
    DocumentOCRError,
    DocumentOCRPolicy,
    IsolatedPDFOCR,
    OCRRuntimePage,
    OCRRuntimeResult,
)
from backend.runtime_policy import DataClassification


TRUSTED_IMAGE_ID = "sha256:" + ("a" * 64)


def descriptor_for(
    content: bytes,
    *,
    document_format: DocumentFormat = DocumentFormat.PDF,
) -> DocumentIntakeDescriptor:
    return DocumentIntakeDescriptor(
        workspace_id="workspace_alpha",
        original_filename="scan.pdf",
        safe_filename="scan.pdf",
        document_format=document_format,
        declared_mime_type="application/pdf",
        detected_mime_type="application/pdf",
        content_encoding=None,
        size_bytes=len(content),
        content_sha256=hashlib.sha256(
            content
        ).hexdigest(),
        intake_fingerprint="f" * 64,
        classification=(
            DataClassification.CONFIDENTIAL
        ),
        warnings=(),
    )


def page_result(
    page_number: int,
    *,
    text: str = "Recognized text",
    width: int = 100,
    height: int = 200,
    png_size: int = 500,
    warnings: tuple[str, ...] = (),
) -> OCRRuntimePage:
    return OCRRuntimePage(
        page_number=page_number,
        text=text,
        width_pixels=width,
        height_pixels=height,
        pixel_count=width * height,
        png_sha256=(
            f"{page_number:x}"[-1] * 64
        ),
        png_size_bytes=png_size,
        warnings=warnings,
    )


def runtime_result(
    *,
    page_count: int = 1,
    pages: tuple[OCRRuntimePage, ...] | None = None,
    warnings: tuple[str, ...] = (),
) -> OCRRuntimeResult:
    return OCRRuntimeResult(
        page_count=page_count,
        pages=pages or (page_result(1),),
        engine="tesseract",
        engine_version="tesseract 5.5.0",
        renderer="pdfium",
        renderer_version="5.8.0",
        runtime_image=(
            "ai-studio-ocr-tesseract:5-v1"
        ),
        runtime_image_id=TRUSTED_IMAGE_ID,
        warnings=warnings,
    )


class FakeRuntime:
    def __init__(
        self,
        result: OCRRuntimeResult,
    ) -> None:
        self.result = result
        self.paths: list[Path] = []
        self.selections: list[
            tuple[int, ...] | None
        ] = []

    def recognize_pdf(
        self,
        *,
        pdf_path: Path,
        page_numbers: tuple[int, ...] | None,
        policy: DocumentOCRPolicy,
    ) -> OCRRuntimeResult:
        assert pdf_path.is_file()
        assert pdf_path.read_bytes()
        self.paths.append(pdf_path)
        self.selections.append(page_numbers)
        return self.result


def test_ocr_normalizes_text_inherits_classification_and_cleans_temp(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    runtime = FakeRuntime(
        runtime_result(
            pages=(
                page_result(
                    1,
                    text="  Alpha\r\nBeta  \r\n",
                ),
            )
        )
    )
    service = IsolatedPDFOCR(
        runtime=runtime,
        temp_root=tmp_path,
    )

    result = service.recognize_pdf(
        descriptor=descriptor_for(content),
        content=content,
        page_numbers=[1],
    )

    assert result.classification == "confidential"
    assert result.pages[0].text == "  Alpha\nBeta"
    assert result.pages[0].character_count == 12
    assert result.source_sha256 == hashlib.sha256(
        content
    ).hexdigest()
    assert runtime.paths
    assert all(
        not path.exists()
        for path in runtime.paths
    )


def test_page_selection_is_sorted_and_deduplicated(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    runtime = FakeRuntime(
        runtime_result(
            page_count=3,
            pages=(
                page_result(1),
                page_result(3),
            ),
        )
    )
    service = IsolatedPDFOCR(
        runtime=runtime,
        temp_root=tmp_path,
    )

    result = service.recognize_pdf(
        descriptor=descriptor_for(content),
        content=content,
        page_numbers=[3, 1, 3],
    )

    assert runtime.selections == [(1, 3)]
    assert [
        page.page_number
        for page in result.pages
    ] == [1, 3]


def test_non_pdf_is_rejected_before_runtime(
    tmp_path: Path,
) -> None:
    content = b"text"
    runtime = FakeRuntime(runtime_result())
    service = IsolatedPDFOCR(
        runtime=runtime,
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(
                content,
                document_format=DocumentFormat.TXT,
            ),
            content=content,
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_FORMAT_UNSUPPORTED"
    )
    assert runtime.paths == []


def test_source_hash_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(runtime_result())
    service = IsolatedPDFOCR(
        runtime=runtime,
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(b"original"),
            content=b"changed",
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_SOURCE_HASH_MISMATCH"
    )
    assert runtime.paths == []


def test_empty_page_selection_is_rejected(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(runtime_result()),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
            page_numbers=[],
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_PAGE_SELECTION_EMPTY"
    )


def test_runtime_page_set_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(
                page_count=2,
                pages=(page_result(2),),
            )
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
            page_numbers=[1],
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_PAGE_MISMATCH"
    )


def test_runtime_pixel_metadata_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    invalid = OCRRuntimePage(
        page_number=1,
        text="text",
        width_pixels=10,
        height_pixels=20,
        pixel_count=201,
        png_sha256="a" * 64,
        png_size_bytes=100,
    )
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(pages=(invalid,))
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_PIXEL_MISMATCH"
    )


def test_page_pixel_limit_is_rechecked_on_host(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(
                pages=(
                    page_result(
                        1,
                        width=100,
                        height=100,
                    ),
                )
            )
        ),
        policy=DocumentOCRPolicy(
            max_page_pixels=9_999,
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_PAGE_PIXEL_LIMIT"
    )


def test_total_text_limit_is_rechecked_on_host(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(
                page_count=2,
                pages=(
                    page_result(1, text="abcd"),
                    page_result(2, text="efgh"),
                ),
            )
        ),
        policy=DocumentOCRPolicy(
            max_total_text_characters=7,
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_TOTAL_TEXT_LIMIT"
    )


def test_public_dict_can_omit_ocr_text(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(runtime_result()),
        temp_root=tmp_path,
    )

    result = service.recognize_pdf(
        descriptor=descriptor_for(content),
        content=content,
    )
    public = result.to_public_dict(
        include_text=False
    )

    assert "text" not in public["pages"][0]
    assert public["pages"][0]["text_sha256"]
    assert public["classification"] == "confidential"


def test_empty_page_adds_stable_warning(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(
                pages=(
                    page_result(
                        1,
                        text=" \r\n ",
                        warnings=("OCR_PAGE_NO_TEXT",),
                    ),
                ),
                warnings=("OCR_PAGE_1_NO_TEXT",),
            )
        ),
        temp_root=tmp_path,
    )

    result = service.recognize_pdf(
        descriptor=descriptor_for(content),
        content=content,
    )

    assert result.pages[0].text == ""
    assert result.pages[0].warnings == (
        "OCR_PAGE_NO_TEXT",
    )
    assert result.warnings == (
        "OCR_PAGE_1_NO_TEXT",
    )


def test_policy_rejects_unsafe_languages() -> None:
    with pytest.raises(ValueError):
        DocumentOCRPolicy(
            languages=("eng;rm -rf",),
        )


def docker_success_payload() -> dict[str, object]:
    return {
        "ok": True,
        "page_count": 1,
        "selected_page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "text": "Text",
                "width_pixels": 10,
                "height_pixels": 20,
                "pixel_count": 200,
                "png_sha256": "b" * 64,
                "png_size_bytes": 100,
                "warnings": [],
            }
        ],
        "engine": "tesseract",
        "engine_version": "tesseract 5.5.0",
        "renderer": "pdfium",
        "renderer_version": "5.8.0",
        "warnings": [],
    }


def test_docker_runtime_uses_trusted_image_id_and_hardening(
    tmp_path: Path,
) -> None:
    calls: list[
        tuple[list[str], dict[str, object]]
    ] = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"{TRUSTED_IMAGE_ID}|p3-001.4a|5-v1\n",
                "",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                docker_success_payload()
            ),
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    result = runtime.recognize_pdf(
        pdf_path=source,
        page_numbers=(1,),
        policy=DocumentOCRPolicy(),
    )

    assert result.runtime_image_id == (
        TRUSTED_IMAGE_ID
    )
    args, kwargs = calls[-1]
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "no-new-privileges=true" in args
    assert "--pull=never" in args
    assert TRUSTED_IMAGE_ID in args
    assert any(
        item.endswith(",readonly")
        and "document.pdf" in item
        for item in args
    )
    assert kwargs["shell"] is False


def test_docker_runtime_rejects_untrusted_image(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            f"{TRUSTED_IMAGE_ID}|wrong-label|5-v1\n",
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        runtime.recognize_pdf(
            pdf_path=source,
            page_numbers=(1,),
            policy=DocumentOCRPolicy(),
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_UNTRUSTED"
    )


def test_docker_runtime_rejects_wrong_runtime_version(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            f"{TRUSTED_IMAGE_ID}|p3-001.4a|4-v1\n",
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        runtime.recognize_pdf(
            pdf_path=source,
            page_numbers=(1,),
            policy=DocumentOCRPolicy(),
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_UNTRUSTED"
    )


def test_docker_runtime_rejects_non_immutable_image_id(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            "local-image-id|p3-001.4a|5-v1\n",
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    status = runtime.status()

    assert status.image_present is True
    assert status.image_trusted is False
    assert status.image_id is None


def test_docker_runtime_filters_failure_details(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"{TRUSTED_IMAGE_ID}|p3-001.4a|5-v1\n",
                "",
            )
        payload = {
            "ok": False,
            "error_code": (
                "DOCUMENT_OCR_ENGINE_FAILED"
            ),
            "message": "secret document text",
            "details": {
                "page_number": "secret page text",
                "returncode": 7,
                "text": "secret output",
            },
        }
        return subprocess.CompletedProcess(
            args,
            2,
            json.dumps(payload),
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        runtime.recognize_pdf(
            pdf_path=source,
            page_numbers=(2,),
            policy=DocumentOCRPolicy(),
        )

    assert captured.value.details == {"returncode": 7}
    assert str(captured.value) == (
        "OCR engine failed to process the page."
    )


def test_docker_runtime_rejects_malformed_protocol(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"{TRUSTED_IMAGE_ID}|p3-001.4a|5-v1\n",
                "",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            "not-json",
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        runtime.recognize_pdf(
            pdf_path=source,
            page_numbers=(1,),
            policy=DocumentOCRPolicy(),
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID"
    )


def test_docker_runtime_rejects_coerced_protocol_types(
    tmp_path: Path,
) -> None:
    def runner(args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                f"{TRUSTED_IMAGE_ID}|p3-001.4a|5-v1\n",
                "",
            )
        payload = docker_success_payload()
        payload["page_count"] = "1"
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(payload),
            "",
        )

    source = tmp_path / "document.pdf"
    source.write_bytes(b"pdf")
    runtime = DockerTesseractOCRRuntime(
        runner=runner,
        resolver=lambda _: "docker",
        enabled=True,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        runtime.recognize_pdf(
            pdf_path=source,
            page_numbers=(1,),
            policy=DocumentOCRPolicy(),
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID"
    )


def test_ocr_rejects_inconsistent_runtime_warning(
    tmp_path: Path,
) -> None:
    content = b"synthetic-pdf"
    service = IsolatedPDFOCR(
        runtime=FakeRuntime(
            runtime_result(
                pages=(
                    page_result(
                        1,
                        text="recognized",
                        warnings=("OCR_PAGE_NO_TEXT",),
                    ),
                ),
            )
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(
        DocumentOCRError
    ) as captured:
        service.recognize_pdf(
            descriptor=descriptor_for(content),
            content=content,
        )

    assert captured.value.code == (
        "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID"
    )
