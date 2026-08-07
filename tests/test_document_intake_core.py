from __future__ import annotations

from io import BytesIO
import hashlib
import zipfile

import pytest

from backend.documents import (
    DocumentFormat,
    DocumentIntakeError,
    DocumentIntakePolicy,
    DocumentIntakeRequest,
    DocumentIntakeService,
)
from backend.runtime_policy import DataClassification


def request(
    filename: str,
    content_type: str | None,
    *,
    workspace_id: str = "workspace-1",
    classification: DataClassification = (
        DataClassification.INTERNAL
    ),
) -> DocumentIntakeRequest:
    return DocumentIntakeRequest(
        workspace_id=workspace_id,
        filename=filename,
        content_type=content_type,
        classification=classification,
    )


def ooxml_bytes(
    *,
    parts: dict[str, bytes],
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=compression,
    ) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def valid_docx() -> bytes:
    return ooxml_bytes(
        parts={
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
        }
    )


def valid_xlsx() -> bytes:
    return ooxml_bytes(
        parts={
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
        }
    )


def assert_error(
    code: str,
    callback,
) -> DocumentIntakeError:
    with pytest.raises(DocumentIntakeError) as captured:
        callback()
    assert captured.value.code == code
    return captured.value


def test_pdf_descriptor_is_deterministic_and_content_free() -> None:
    service = DocumentIntakeService()
    payload = b"%PDF-1.7\nexample\n%%EOF"
    intake_request = request(
        "Документ.pdf",
        "application/pdf",
        classification=DataClassification.CONFIDENTIAL,
    )

    first = service.inspect_bytes(
        intake_request,
        payload,
    )
    second = service.inspect_bytes(
        intake_request,
        payload,
    )

    assert first.document_format is DocumentFormat.PDF
    assert first.detected_mime_type == "application/pdf"
    assert first.classification is (
        DataClassification.CONFIDENTIAL
    )
    assert first.content_sha256 == second.content_sha256
    assert (
        first.intake_fingerprint
        == second.intake_fingerprint
    )
    public = first.to_public_dict()
    assert payload not in public.values()
    assert "content" not in public
    assert "bytes" not in public


def test_fingerprint_is_workspace_scoped() -> None:
    service = DocumentIntakeService()
    payload = b"%PDF-1.4\n%%EOF"

    first = service.inspect_bytes(
        request("a.pdf", "application/pdf"),
        payload,
    )
    second = service.inspect_bytes(
        request(
            "a.pdf",
            "application/pdf",
            workspace_id="workspace-2",
        ),
        payload,
    )

    assert first.content_sha256 == second.content_sha256
    assert (
        first.intake_fingerprint
        != second.intake_fingerprint
    )


def test_generic_mime_is_allowed_with_warning() -> None:
    descriptor = DocumentIntakeService().inspect_bytes(
        request("notes.txt", "application/octet-stream"),
        "Привет".encode("utf-8"),
    )

    assert descriptor.warnings == (
        "DECLARED_MIME_GENERIC",
    )


def test_mime_mismatch_fails_closed() -> None:
    assert_error(
        "DOCUMENT_MIME_MISMATCH",
        lambda: DocumentIntakeService().inspect_bytes(
            request("file.pdf", "text/plain"),
            b"%PDF-1.4\n%%EOF",
        ),
    )


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("", "DOCUMENT_FILENAME_EMPTY"),
        ("../secret.pdf", "DOCUMENT_FILENAME_UNSAFE"),
        (r"..\secret.pdf", "DOCUMENT_FILENAME_UNSAFE"),
        ("CON.txt", "DOCUMENT_FILENAME_RESERVED"),
        ("file", "DOCUMENT_EXTENSION_MISSING"),
        ("file.exe", "DOCUMENT_FORMAT_UNSUPPORTED"),
    ],
)
def test_unsafe_or_unsupported_filename_fails(
    filename: str,
    code: str,
) -> None:
    assert_error(
        code,
        lambda: DocumentIntakeService().inspect_bytes(
            request(filename, "application/octet-stream"),
            b"payload",
        ),
    )


def test_empty_document_fails() -> None:
    assert_error(
        "DOCUMENT_EMPTY",
        lambda: DocumentIntakeService().inspect_bytes(
            request("empty.txt", "text/plain"),
            b"",
        ),
    )


def test_bounded_stream_rejects_oversize_content() -> None:
    service = DocumentIntakeService(
        DocumentIntakePolicy(max_file_bytes=8)
    )
    assert_error(
        "DOCUMENT_TOO_LARGE",
        lambda: service.inspect_stream(
            request("large.txt", "text/plain"),
            BytesIO(b"123456789"),
        ),
    )


def test_pdf_signature_mismatch_fails() -> None:
    assert_error(
        "DOCUMENT_SIGNATURE_MISMATCH",
        lambda: DocumentIntakeService().inspect_bytes(
            request("fake.pdf", "application/pdf"),
            b"not a pdf",
        ),
    )


@pytest.mark.parametrize(
    ("filename", "mime", "payload", "format_value"),
    [
        (
            "report.docx",
            (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            valid_docx(),
            DocumentFormat.DOCX,
        ),
        (
            "table.xlsx",
            (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            valid_xlsx(),
            DocumentFormat.XLSX,
        ),
    ],
)
def test_valid_ooxml_is_accepted(
    filename: str,
    mime: str,
    payload: bytes,
    format_value: DocumentFormat,
) -> None:
    descriptor = DocumentIntakeService().inspect_bytes(
        request(filename, mime),
        payload,
    )
    assert descriptor.document_format is format_value


def test_docx_cannot_authorize_xlsx_container() -> None:
    assert_error(
        "DOCUMENT_OOXML_PARTS_MISSING",
        lambda: DocumentIntakeService().inspect_bytes(
            request(
                "fake.docx",
                (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            ),
            valid_xlsx(),
        ),
    )


def test_ooxml_path_traversal_fails() -> None:
    payload = ooxml_bytes(
        parts={
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
            "../escape.txt": b"blocked",
        }
    )
    assert_error(
        "DOCUMENT_ARCHIVE_PATH_UNSAFE",
        lambda: DocumentIntakeService().inspect_bytes(
            request("unsafe.docx", "application/zip"),
            payload,
        ),
    )


def test_ooxml_macro_part_fails() -> None:
    payload = ooxml_bytes(
        parts={
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
            "word/vbaProject.bin": b"macro",
        }
    )
    assert_error(
        "DOCUMENT_ARCHIVE_MACRO",
        lambda: DocumentIntakeService().inspect_bytes(
            request("macro.docx", "application/zip"),
            payload,
        ),
    )


def test_ooxml_entry_count_limit_fails() -> None:
    payload = valid_docx()
    service = DocumentIntakeService(
        DocumentIntakePolicy(max_archive_entries=1)
    )
    assert_error(
        "DOCUMENT_ARCHIVE_ENTRY_LIMIT",
        lambda: service.inspect_bytes(
            request("report.docx", "application/zip"),
            payload,
        ),
    )


def test_ooxml_uncompressed_limit_fails() -> None:
    payload = ooxml_bytes(
        parts={
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"x" * 64,
        },
        compression=zipfile.ZIP_STORED,
    )
    service = DocumentIntakeService(
        DocumentIntakePolicy(
            max_archive_uncompressed_bytes=32
        )
    )
    assert_error(
        "DOCUMENT_ARCHIVE_TOO_LARGE",
        lambda: service.inspect_bytes(
            request("report.docx", "application/zip"),
            payload,
        ),
    )


@pytest.mark.parametrize(
    ("payload", "encoding"),
    [
        ("Привет".encode("utf-8"), "utf-8"),
        ("Привет".encode("cp1251"), "windows-1251"),
        ("Привет".encode("utf-16"), "utf-16"),
    ],
)
def test_txt_encodings_are_detected(
    payload: bytes,
    encoding: str,
) -> None:
    descriptor = DocumentIntakeService().inspect_bytes(
        request("notes.txt", "text/plain"),
        payload,
    )
    assert descriptor.content_encoding == encoding


def test_binary_txt_fails() -> None:
    assert_error(
        "DOCUMENT_TEXT_BINARY",
        lambda: DocumentIntakeService().inspect_bytes(
            request("binary.txt", "text/plain"),
            b"hello\x00world",
        ),
    )


def test_workspace_identifier_is_required() -> None:
    assert_error(
        "DOCUMENT_WORKSPACE_INVALID",
        lambda: DocumentIntakeService().inspect_bytes(
            request(
                "notes.txt",
                "text/plain",
                workspace_id=" ",
            ),
            b"hello",
        ),
    )


def test_descriptor_does_not_retain_input_buffer() -> None:
    payload = bytearray(b"%PDF-1.4\n%%EOF")
    descriptor = DocumentIntakeService().inspect_bytes(
        request("file.pdf", "application/pdf"),
        bytes(payload),
    )
    payload[:] = b"x" * len(payload)

    assert descriptor.content_sha256 != (
        hashlib.sha256(bytes(payload)).hexdigest()
    )
