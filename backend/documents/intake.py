from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
from typing import BinaryIO
import unicodedata
import zipfile

from backend.runtime_policy import DataClassification


_MIB = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_GENERIC_MIME_TYPES = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
}
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_WINDOWS_CHARACTER_RE = re.compile(r'[<>:"|?*]')


class DocumentFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    TXT = "txt"


_FORMAT_MIME_TYPES: dict[DocumentFormat, frozenset[str]] = {
    DocumentFormat.PDF: frozenset({"application/pdf"}),
    DocumentFormat.DOCX: frozenset(
        {
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
            "application/zip",
        }
    ),
    DocumentFormat.XLSX: frozenset(
        {
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet",
            "application/zip",
        }
    ),
    DocumentFormat.TXT: frozenset({"text/plain"}),
}
_DETECTED_MIME_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    DocumentFormat.XLSX: (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    DocumentFormat.TXT: "text/plain",
}
_REQUIRED_OOXML_PARTS: dict[DocumentFormat, frozenset[str]] = {
    DocumentFormat.DOCX: frozenset(
        {"[Content_Types].xml", "word/document.xml"}
    ),
    DocumentFormat.XLSX: frozenset(
        {"[Content_Types].xml", "xl/workbook.xml"}
    ),
}


class DocumentIntakeError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class DocumentIntakePolicy:
    max_file_bytes: int = 25 * _MIB
    max_archive_entries: int = 2_048
    max_archive_uncompressed_bytes: int = 100 * _MIB
    max_archive_entry_bytes: int = 50 * _MIB
    max_archive_compression_ratio: int = 100
    max_filename_chars: int = 240

    def __post_init__(self) -> None:
        positive_fields = (
            "max_file_bytes",
            "max_archive_entries",
            "max_archive_uncompressed_bytes",
            "max_archive_entry_bytes",
            "max_archive_compression_ratio",
            "max_filename_chars",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class DocumentIntakeRequest:
    workspace_id: str
    filename: str
    content_type: str | None
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class DocumentIntakeDescriptor:
    workspace_id: str
    original_filename: str
    safe_filename: str
    document_format: DocumentFormat
    declared_mime_type: str
    detected_mime_type: str
    content_encoding: str | None
    size_bytes: int
    content_sha256: str
    intake_fingerprint: str
    classification: DataClassification
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "original_filename": self.original_filename,
            "safe_filename": self.safe_filename,
            "document_format": self.document_format.value,
            "declared_mime_type": self.declared_mime_type,
            "detected_mime_type": self.detected_mime_type,
            "content_encoding": self.content_encoding,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
            "intake_fingerprint": self.intake_fingerprint,
            "classification": self.classification.value,
            "warnings": list(self.warnings),
        }


class DocumentIntakeService:
    def __init__(
        self,
        policy: DocumentIntakePolicy | None = None,
    ) -> None:
        self._policy = policy or DocumentIntakePolicy()

    def inspect_bytes(
        self,
        request: DocumentIntakeRequest,
        content: bytes,
    ) -> DocumentIntakeDescriptor:
        return self.inspect_stream(
            request,
            io.BytesIO(content),
        )

    def inspect_stream(
        self,
        request: DocumentIntakeRequest,
        stream: BinaryIO,
    ) -> DocumentIntakeDescriptor:
        workspace_id = self._validate_workspace_id(
            request.workspace_id
        )
        original_filename, safe_filename = (
            self._normalize_filename(request.filename)
        )
        document_format = self._format_from_filename(
            safe_filename
        )
        declared_mime = self._normalize_mime(
            request.content_type
        )
        warnings = self._validate_declared_mime(
            document_format,
            declared_mime,
        )
        content = self._read_bounded(stream)
        if not content:
            raise DocumentIntakeError(
                "DOCUMENT_EMPTY",
                "Document content is empty.",
            )

        detected_mime, encoding = self._validate_content(
            document_format,
            content,
        )
        content_sha256 = hashlib.sha256(content).hexdigest()
        fingerprint = self._fingerprint(
            workspace_id=workspace_id,
            safe_filename=safe_filename,
            document_format=document_format,
            detected_mime=detected_mime,
            size_bytes=len(content),
            content_sha256=content_sha256,
            classification=request.classification,
        )

        return DocumentIntakeDescriptor(
            workspace_id=workspace_id,
            original_filename=original_filename,
            safe_filename=safe_filename,
            document_format=document_format,
            declared_mime_type=declared_mime,
            detected_mime_type=detected_mime,
            content_encoding=encoding,
            size_bytes=len(content),
            content_sha256=content_sha256,
            intake_fingerprint=fingerprint,
            classification=request.classification,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_workspace_id(workspace_id: str) -> str:
        value = workspace_id.strip()
        if (
            not value
            or len(value) > 128
            or _CONTROL_CHARACTER_RE.search(value)
        ):
            raise DocumentIntakeError(
                "DOCUMENT_WORKSPACE_INVALID",
                "Workspace identifier is invalid.",
            )
        return value

    def _normalize_filename(
        self,
        filename: str,
    ) -> tuple[str, str]:
        original = filename.strip()
        normalized = unicodedata.normalize(
            "NFKC",
            original,
        ).strip()

        if not normalized:
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_EMPTY",
                "Document filename is empty.",
            )
        if len(normalized) > self._policy.max_filename_chars:
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_TOO_LONG",
                "Document filename exceeds the configured limit.",
            )
        if (
            "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
        ):
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_UNSAFE",
                "Document filename must not contain a path.",
            )
        if _CONTROL_CHARACTER_RE.search(normalized):
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_UNSAFE",
                "Document filename contains control characters.",
            )

        safe = _UNSAFE_WINDOWS_CHARACTER_RE.sub(
            "_",
            normalized,
        )
        safe = re.sub(r"\s+", " ", safe).strip(" .")
        if not safe:
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_UNSAFE",
                "Document filename cannot be normalized safely.",
            )

        base_name = safe.rsplit(".", 1)[0].casefold()
        if base_name in _WINDOWS_RESERVED_NAMES:
            raise DocumentIntakeError(
                "DOCUMENT_FILENAME_RESERVED",
                "Document filename is reserved by the operating system.",
            )
        return original, safe

    @staticmethod
    def _format_from_filename(
        filename: str,
    ) -> DocumentFormat:
        if "." not in filename:
            raise DocumentIntakeError(
                "DOCUMENT_EXTENSION_MISSING",
                "Document filename has no extension.",
            )
        extension = filename.rsplit(".", 1)[1].casefold()
        try:
            return DocumentFormat(extension)
        except ValueError as exc:
            raise DocumentIntakeError(
                "DOCUMENT_FORMAT_UNSUPPORTED",
                "Only PDF, DOCX, XLSX and TXT are accepted.",
                details={"extension": extension},
            ) from exc

    @staticmethod
    def _normalize_mime(
        content_type: str | None,
    ) -> str:
        if content_type is None:
            return ""
        return (
            content_type.split(";", 1)[0]
            .strip()
            .casefold()
        )

    @staticmethod
    def _validate_declared_mime(
        document_format: DocumentFormat,
        declared_mime: str,
    ) -> list[str]:
        if declared_mime in _GENERIC_MIME_TYPES:
            return ["DECLARED_MIME_GENERIC"]

        accepted = _FORMAT_MIME_TYPES[document_format]
        if declared_mime not in accepted:
            raise DocumentIntakeError(
                "DOCUMENT_MIME_MISMATCH",
                "Declared MIME type does not match the file extension.",
                details={
                    "declared_mime_type": declared_mime,
                    "document_format": document_format.value,
                },
            )
        return []

    def _read_bounded(
        self,
        stream: BinaryIO,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0

        while True:
            remaining = (
                self._policy.max_file_bytes
                - total
                + 1
            )
            chunk = stream.read(
                min(_READ_CHUNK_BYTES, remaining)
            )
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise DocumentIntakeError(
                    "DOCUMENT_STREAM_INVALID",
                    "Document stream must return bytes.",
                )
            chunks.append(chunk)
            total += len(chunk)
            if total > self._policy.max_file_bytes:
                raise DocumentIntakeError(
                    "DOCUMENT_TOO_LARGE",
                    "Document exceeds the configured size limit.",
                    details={
                        "max_file_bytes": (
                            self._policy.max_file_bytes
                        )
                    },
                )

        return b"".join(chunks)

    def _validate_content(
        self,
        document_format: DocumentFormat,
        content: bytes,
    ) -> tuple[str, str | None]:
        if document_format is DocumentFormat.PDF:
            if not content.startswith(b"%PDF-"):
                raise DocumentIntakeError(
                    "DOCUMENT_SIGNATURE_MISMATCH",
                    "PDF signature is missing.",
                )
            return _DETECTED_MIME_TYPES[document_format], None

        if document_format in {
            DocumentFormat.DOCX,
            DocumentFormat.XLSX,
        }:
            self._validate_ooxml(
                document_format,
                content,
            )
            return _DETECTED_MIME_TYPES[document_format], None

        encoding = self._validate_text(content)
        return _DETECTED_MIME_TYPES[document_format], encoding

    def _validate_ooxml(
        self,
        document_format: DocumentFormat,
        content: bytes,
    ) -> None:
        if not content.startswith(b"PK"):
            raise DocumentIntakeError(
                "DOCUMENT_SIGNATURE_MISMATCH",
                "OOXML ZIP signature is missing.",
            )

        try:
            archive = zipfile.ZipFile(
                io.BytesIO(content),
                mode="r",
            )
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentIntakeError(
                "DOCUMENT_ARCHIVE_INVALID",
                "OOXML container is not a valid ZIP archive.",
            ) from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > self._policy.max_archive_entries:
                raise DocumentIntakeError(
                    "DOCUMENT_ARCHIVE_ENTRY_LIMIT",
                    "OOXML archive contains too many entries.",
                )

            names: set[str] = set()
            uncompressed_total = 0
            for info in infos:
                normalized_name = self._validate_archive_name(
                    info.filename
                )
                name_key = normalized_name.casefold()
                if name_key in names:
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_DUPLICATE_ENTRY",
                        "OOXML archive contains duplicate entries.",
                    )
                names.add(name_key)

                if info.flag_bits & 0x1:
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_ENCRYPTED",
                        "Encrypted OOXML archives are not accepted.",
                    )

                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_LINK",
                        "OOXML archive links are not accepted.",
                    )

                if info.file_size > (
                    self._policy.max_archive_entry_bytes
                ):
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_ENTRY_TOO_LARGE",
                        "OOXML archive entry exceeds the configured limit.",
                    )

                uncompressed_total += info.file_size
                if uncompressed_total > (
                    self._policy
                    .max_archive_uncompressed_bytes
                ):
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_TOO_LARGE",
                        "OOXML uncompressed size exceeds the limit.",
                    )

                compressed_size = max(
                    info.compress_size,
                    1,
                )
                if (
                    info.file_size >= _MIB
                    and info.file_size
                    > compressed_size
                    * self._policy
                    .max_archive_compression_ratio
                ):
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_RATIO_LIMIT",
                        "OOXML compression ratio exceeds the limit.",
                    )

                lower_name = normalized_name.casefold()
                if lower_name.endswith(
                    "vbaproject.bin"
                ):
                    raise DocumentIntakeError(
                        "DOCUMENT_ARCHIVE_MACRO",
                        "Macro-enabled OOXML content is not accepted.",
                    )

            required = _REQUIRED_OOXML_PARTS[
                document_format
            ]
            missing = sorted(
                part
                for part in required
                if part.casefold() not in names
            )
            if missing:
                raise DocumentIntakeError(
                    "DOCUMENT_OOXML_PARTS_MISSING",
                    "OOXML document is missing required parts.",
                    details={"missing_parts": missing},
                )

    @staticmethod
    def _validate_archive_name(name: str) -> str:
        if (
            not name
            or "\\" in name
            or _CONTROL_CHARACTER_RE.search(name)
        ):
            raise DocumentIntakeError(
                "DOCUMENT_ARCHIVE_PATH_UNSAFE",
                "OOXML archive contains an unsafe path.",
            )

        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or any(
                part in {"", ".", ".."}
                for part in path.parts
            )
        ):
            raise DocumentIntakeError(
                "DOCUMENT_ARCHIVE_PATH_UNSAFE",
                "OOXML archive contains path traversal.",
            )
        return path.as_posix()

    @staticmethod
    def _validate_text(content: bytes) -> str:
        candidates: list[tuple[str, str]] = []
        if content.startswith(b"\xef\xbb\xbf"):
            candidates.append(("utf-8-sig", "utf-8"))
        elif content.startswith(
            (b"\xff\xfe", b"\xfe\xff")
        ):
            candidates.append(("utf-16", "utf-16"))
        else:
            candidates.extend(
                [
                    ("utf-8", "utf-8"),
                    ("cp1251", "windows-1251"),
                ]
            )

        decoded: str | None = None
        public_encoding: str | None = None
        for codec, label in candidates:
            try:
                decoded = content.decode(codec)
                public_encoding = label
                break
            except UnicodeDecodeError:
                continue

        if decoded is None or public_encoding is None:
            raise DocumentIntakeError(
                "DOCUMENT_TEXT_ENCODING_UNSUPPORTED",
                "TXT content encoding is unsupported.",
            )

        if "\x00" in decoded:
            raise DocumentIntakeError(
                "DOCUMENT_TEXT_BINARY",
                "TXT content contains binary NUL characters.",
            )

        if decoded:
            accepted = sum(
                1
                for char in decoded
                if (
                    char.isprintable()
                    or char in "\r\n\t"
                )
            )
            ratio = accepted / len(decoded)
            if ratio < 0.85:
                raise DocumentIntakeError(
                    "DOCUMENT_TEXT_BINARY",
                    "TXT content appears to be binary.",
                )

        return public_encoding

    @staticmethod
    def _fingerprint(
        *,
        workspace_id: str,
        safe_filename: str,
        document_format: DocumentFormat,
        detected_mime: str,
        size_bytes: int,
        content_sha256: str,
        classification: DataClassification,
    ) -> str:
        canonical = json.dumps(
            {
                "classification": classification.value,
                "content_sha256": content_sha256,
                "detected_mime_type": detected_mime,
                "document_format": document_format.value,
                "safe_filename": safe_filename,
                "size_bytes": size_bytes,
                "workspace_id": workspace_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
