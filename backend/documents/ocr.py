from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Protocol
import unicodedata

from backend.documents.intake import (
    DocumentFormat,
    DocumentIntakeDescriptor,
)


_MIB = 1024 * 1024
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_]+$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_DOCUMENT_WARNING_RE = re.compile(
    r"^OCR_PAGE_[1-9][0-9]*_NO_TEXT$"
)
_PAGE_WARNING_CODES = {
    "OCR_ENGINE_STDERR",
    "OCR_PAGE_NO_TEXT",
}
_SAFE_ERROR_DETAIL_KEYS = {
    "page_number",
    "page_count",
    "selected_page_count",
    "pixel_count",
    "total_pixels",
    "png_size_bytes",
    "characters",
    "timeout_seconds",
    "returncode",
}
_RUNTIME_ERROR_MESSAGES = {
    "DOCUMENT_OCR_LANGUAGES_INVALID": (
        "OCR languages are invalid."
    ),
    "DOCUMENT_OCR_PAGE_SELECTION_INVALID": (
        "OCR page selection is invalid."
    ),
    "DOCUMENT_OCR_PAGE_SELECTION_EMPTY": (
        "At least one OCR page must be selected."
    ),
    "DOCUMENT_OCR_SELECTED_PAGE_LIMIT": (
        "Selected OCR page count exceeds the configured limit."
    ),
    "DOCUMENT_OCR_PAGE_OUT_OF_RANGE": (
        "OCR page selection is outside the PDF range."
    ),
    "DOCUMENT_OCR_SOURCE_MISSING": (
        "OCR source PDF is missing."
    ),
    "DOCUMENT_OCR_PDF_INVALID": (
        "PDF cannot be opened by the isolated renderer."
    ),
    "DOCUMENT_OCR_PDF_EMPTY": "PDF has no pages.",
    "DOCUMENT_OCR_PDF_PAGE_LIMIT": (
        "PDF page count exceeds the configured OCR limit."
    ),
    "DOCUMENT_OCR_PAGE_PIXEL_LIMIT": (
        "Rendered OCR page exceeds the pixel limit."
    ),
    "DOCUMENT_OCR_TOTAL_PIXEL_LIMIT": (
        "OCR render workload exceeds the total pixel limit."
    ),
    "DOCUMENT_OCR_PAGE_IMAGE_LIMIT": (
        "Rendered OCR page image exceeds the byte limit."
    ),
    "DOCUMENT_OCR_PAGE_TIMEOUT": (
        "OCR page processing exceeded the configured timeout."
    ),
    "DOCUMENT_OCR_ENGINE_FAILED": (
        "OCR engine failed to process the page."
    ),
    "DOCUMENT_OCR_PAGE_TEXT_LIMIT": (
        "OCR page text exceeds the configured limit."
    ),
    "DOCUMENT_OCR_TOTAL_TEXT_LIMIT": (
        "OCR text exceeds the configured total limit."
    ),
    "DOCUMENT_OCR_RUNTIME_INTERNAL_ERROR": (
        "Isolated OCR runtime failed unexpectedly."
    ),
    "DOCUMENT_OCR_RUNTIME_FAILED": (
        "OCR runtime failed."
    ),
}


class DocumentOCRError(RuntimeError):
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


@dataclass(frozen=True, slots=True)
class DocumentOCRPolicy:
    render_dpi: int = 200
    max_pdf_pages: int = 5_000
    max_selected_pages: int = 100
    max_page_pixels: int = 25_000_000
    max_total_pixels: int = 500_000_000
    max_png_bytes: int = 32 * _MIB
    max_text_characters_per_page: int = 500_000
    max_total_text_characters: int = 5_000_000
    timeout_seconds_per_page: int = 60
    max_runtime_seconds: int = 900
    languages: tuple[str, ...] = ("eng", "rus")
    page_segmentation_mode: int = 3

    def __post_init__(self) -> None:
        positive_fields = (
            "render_dpi",
            "max_pdf_pages",
            "max_selected_pages",
            "max_page_pixels",
            "max_total_pixels",
            "max_png_bytes",
            "max_text_characters_per_page",
            "max_total_text_characters",
            "timeout_seconds_per_page",
            "max_runtime_seconds",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

        if not self.languages:
            raise ValueError("languages must not be empty")
        for language in self.languages:
            if not _LANGUAGE_RE.fullmatch(language):
                raise ValueError(
                    "languages must contain only safe OCR tokens"
                )
        if not 0 <= self.page_segmentation_mode <= 13:
            raise ValueError(
                "page_segmentation_mode must be between 0 and 13"
            )


@dataclass(frozen=True, slots=True)
class OCRRuntimeStatus:
    enabled: bool
    docker_cli_available: bool
    image_present: bool
    image_trusted: bool
    image: str
    image_id: str | None
    runtime_version: str | None
    error: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "docker_cli_available": self.docker_cli_available,
            "image_present": self.image_present,
            "image_trusted": self.image_trusted,
            "image": self.image,
            "image_id": self.image_id,
            "runtime_version": self.runtime_version,
            "network_mode": "none",
            "root_filesystem_read_only": True,
            "input_mount_read_only": True,
            "capabilities_dropped": True,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class OCRRuntimePage:
    page_number: int
    text: str
    width_pixels: int
    height_pixels: int
    pixel_count: int
    png_sha256: str
    png_size_bytes: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class OCRRuntimeResult:
    page_count: int
    pages: tuple[OCRRuntimePage, ...]
    engine: str
    engine_version: str
    renderer: str
    renderer_version: str
    runtime_image: str
    runtime_image_id: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class PDFOCRRuntime(Protocol):
    def recognize_pdf(
        self,
        *,
        pdf_path: Path,
        page_numbers: tuple[int, ...] | None,
        policy: DocumentOCRPolicy,
    ) -> OCRRuntimeResult:
        ...


class DockerTesseractOCRRuntime:
    DEFAULT_IMAGE = "ai-studio-ocr-tesseract:5-v1"
    TRUST_LABEL = "p3-001.4a"
    EXPECTED_RUNTIME_VERSION = "5-v1"

    def __init__(
        self,
        *,
        image: str | None = None,
        docker_executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]]
        | None = None,
        resolver: Callable[[str], str | None] | None = None,
        enabled: bool | None = None,
        cpu_limit: float = 1.0,
        memory_mb: int = 768,
        pids_limit: int = 64,
    ) -> None:
        self._image = (
            image
            or os.getenv("AI_STUDIO_OCR_IMAGE", "").strip()
            or self.DEFAULT_IMAGE
        )
        self._docker = (
            docker_executable
            or os.getenv("AI_STUDIO_DOCKER_EXECUTABLE", "").strip()
            or "docker"
        )
        self._runner = runner or subprocess.run
        self._resolver = resolver or shutil.which
        self._enabled_override = enabled
        self._cpu_limit = cpu_limit
        self._memory_mb = memory_mb
        self._pids_limit = pids_limit

        if self._cpu_limit <= 0:
            raise ValueError("cpu_limit must be positive")
        if self._memory_mb < 256:
            raise ValueError("memory_mb must be at least 256")
        if self._pids_limit <= 0:
            raise ValueError("pids_limit must be positive")

    def status(self) -> OCRRuntimeStatus:
        configured = (
            self._enabled_override
            if self._enabled_override is not None
            else self._configured_enabled()
        )
        resolved = self._resolver(self._docker)
        cli_available = bool(resolved)

        if not configured:
            return OCRRuntimeStatus(
                enabled=False,
                docker_cli_available=cli_available,
                image_present=False,
                image_trusted=False,
                image=self._image,
                image_id=None,
                runtime_version=None,
                error="OCR runtime is disabled.",
            )
        if not cli_available:
            return OCRRuntimeStatus(
                enabled=False,
                docker_cli_available=False,
                image_present=False,
                image_trusted=False,
                image=self._image,
                image_id=None,
                runtime_version=None,
                error="Docker CLI is unavailable.",
            )

        try:
            inspected = self._runner(
                [
                    resolved or self._docker,
                    "image",
                    "inspect",
                    self._image,
                    "--format",
                    (
                        '{{.Id}}|'
                        '{{index .Config.Labels '
                        '"org.ai-studio.ocr-runtime"}}|'
                        '{{index .Config.Labels '
                        '"org.ai-studio.ocr-version"}}'
                    ),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                shell=False,
            )
        except Exception:
            return OCRRuntimeStatus(
                enabled=False,
                docker_cli_available=True,
                image_present=False,
                image_trusted=False,
                image=self._image,
                image_id=None,
                runtime_version=None,
                error="OCR runtime image inspection failed.",
            )

        if inspected.returncode != 0:
            return OCRRuntimeStatus(
                enabled=False,
                docker_cli_available=True,
                image_present=False,
                image_trusted=False,
                image=self._image,
                image_id=None,
                runtime_version=None,
                error="OCR runtime image is unavailable.",
            )

        raw = (inspected.stdout or "").strip()
        image_id, first_separator, remainder = raw.partition("|")
        trust_label, second_separator, runtime_version = (
            remainder.partition("|")
        )
        normalized_image_id = image_id.strip()
        normalized_runtime_version = runtime_version.strip()
        valid_image_id = bool(
            _IMAGE_ID_RE.fullmatch(normalized_image_id)
        )
        valid_runtime_version = bool(
            _IMAGE_VERSION_RE.fullmatch(
                normalized_runtime_version
            )
        )
        trusted = (
            bool(first_separator)
            and bool(second_separator)
            and valid_image_id
            and trust_label.strip() == self.TRUST_LABEL
            and valid_runtime_version
            and normalized_runtime_version
            == self.EXPECTED_RUNTIME_VERSION
        )
        return OCRRuntimeStatus(
            enabled=trusted,
            docker_cli_available=True,
            image_present=True,
            image_trusted=trusted,
            image=self._image,
            image_id=(
                normalized_image_id
                if valid_image_id
                else None
            ),
            runtime_version=(
                normalized_runtime_version
                if valid_runtime_version
                else None
            ),
            error=(
                ""
                if trusted
                else "OCR runtime image is not trusted."
            ),
        )

    def recognize_pdf(
        self,
        *,
        pdf_path: Path,
        page_numbers: tuple[int, ...] | None,
        policy: DocumentOCRPolicy,
    ) -> OCRRuntimeResult:
        path = pdf_path.resolve()
        if not path.is_file():
            raise DocumentOCRError(
                "DOCUMENT_OCR_SOURCE_MISSING",
                "OCR source PDF is missing.",
            )

        status = self.status()
        if not status.enabled:
            if not status.docker_cli_available:
                code = "DOCUMENT_OCR_RUNTIME_UNAVAILABLE"
            elif not status.image_present:
                code = "DOCUMENT_OCR_RUNTIME_IMAGE_MISSING"
            else:
                code = "DOCUMENT_OCR_RUNTIME_UNTRUSTED"
            raise DocumentOCRError(
                code,
                status.error or "OCR runtime is unavailable.",
            )

        resolved = self._resolver(self._docker) or self._docker
        mount = (
            f"type=bind,src={path},"
            "dst=/input/document.pdf,readonly"
        )
        selected = (
            ""
            if page_numbers is None
            else ",".join(str(value) for value in page_numbers)
        )
        args = [
            resolved,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            str(self._pids_limit),
            "--cpus",
            str(self._cpu_limit),
            "--memory",
            f"{self._memory_mb}m",
            "--memory-swap",
            f"{self._memory_mb}m",
            "--user",
            "65534:65534",
            "--ulimit",
            "nofile=256:256",
            "--mount",
            mount,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
            "--env",
            "HOME=/tmp",
            status.image_id or self._image,
            "--input",
            "/input/document.pdf",
            "--pages",
            selected,
            "--dpi",
            str(policy.render_dpi),
            "--max-pdf-pages",
            str(policy.max_pdf_pages),
            "--max-selected-pages",
            str(policy.max_selected_pages),
            "--max-page-pixels",
            str(policy.max_page_pixels),
            "--max-total-pixels",
            str(policy.max_total_pixels),
            "--max-png-bytes",
            str(policy.max_png_bytes),
            "--max-page-characters",
            str(policy.max_text_characters_per_page),
            "--max-total-characters",
            str(policy.max_total_text_characters),
            "--page-timeout",
            str(policy.timeout_seconds_per_page),
            "--languages",
            "+".join(policy.languages),
            "--psm",
            str(policy.page_segmentation_mode),
        ]

        try:
            result = self._runner(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=policy.max_runtime_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_TIMEOUT",
                "OCR runtime exceeded the configured timeout.",
                details={
                    "timeout_seconds": (
                        policy.max_runtime_seconds
                    )
                },
            ) from exc
        except Exception as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_FAILED",
                "OCR runtime execution failed.",
            ) from exc

        stdout = result.stdout or ""
        maximum_output = (
            policy.max_total_text_characters * 6
            + 1_000_000
        )
        if len(stdout) > maximum_output:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_OUTPUT_LIMIT",
                "OCR runtime output exceeds the safe limit.",
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned an invalid response.",
                details={"returncode": result.returncode},
            ) from exc

        if not isinstance(payload, dict):
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned an invalid response.",
            )
        if result.returncode != 0 or payload.get("ok") is not True:
            requested_code = payload.get("error_code")
            code = (
                requested_code
                if isinstance(requested_code, str)
                and requested_code in _RUNTIME_ERROR_MESSAGES
                else "DOCUMENT_OCR_RUNTIME_FAILED"
            )
            raise DocumentOCRError(
                code,
                _RUNTIME_ERROR_MESSAGES[code],
                details=self._safe_error_details(
                    payload.get("details")
                ),
            )

        return self._parse_success(
            payload=payload,
            status=status,
            policy=policy,
        )

    def _parse_success(
        self,
        *,
        payload: dict[str, Any],
        status: OCRRuntimeStatus,
        policy: DocumentOCRPolicy,
    ) -> OCRRuntimeResult:
        try:
            page_count = self._protocol_int(
                payload,
                "page_count",
                minimum=1,
                maximum=policy.max_pdf_pages,
            )
            selected_page_count = self._protocol_int(
                payload,
                "selected_page_count",
                minimum=1,
                maximum=policy.max_selected_pages,
            )
            raw_pages = payload["pages"]
            if not isinstance(raw_pages, list):
                raise TypeError
            if (
                len(raw_pages) != selected_page_count
                or len(raw_pages) > policy.max_selected_pages
            ):
                raise ValueError
            pages: list[OCRRuntimePage] = []
            seen_page_numbers: set[int] = set()
            total_pixels = 0
            total_characters = 0
            for raw in raw_pages:
                if not isinstance(raw, dict):
                    raise TypeError
                page_number = self._protocol_int(
                    raw,
                    "page_number",
                    minimum=1,
                    maximum=page_count,
                )
                if page_number in seen_page_numbers:
                    raise ValueError
                seen_page_numbers.add(page_number)
                width_pixels = self._protocol_int(
                    raw,
                    "width_pixels",
                    minimum=1,
                )
                height_pixels = self._protocol_int(
                    raw,
                    "height_pixels",
                    minimum=1,
                )
                pixel_count = self._protocol_int(
                    raw,
                    "pixel_count",
                    minimum=1,
                    maximum=policy.max_page_pixels,
                )
                if width_pixels * height_pixels != pixel_count:
                    raise ValueError
                total_pixels += pixel_count
                if total_pixels > policy.max_total_pixels:
                    raise ValueError
                png_size_bytes = self._protocol_int(
                    raw,
                    "png_size_bytes",
                    minimum=1,
                    maximum=policy.max_png_bytes,
                )
                text = raw.get("text")
                if not isinstance(text, str):
                    raise TypeError
                if (
                    len(text)
                    > policy.max_text_characters_per_page
                ):
                    raise ValueError
                total_characters += len(text)
                if (
                    total_characters
                    > policy.max_total_text_characters
                ):
                    raise ValueError
                png_sha256 = raw.get("png_sha256")
                if (
                    not isinstance(png_sha256, str)
                    or not re.fullmatch(
                        r"[0-9a-f]{64}",
                        png_sha256,
                    )
                ):
                    raise ValueError
                page_warnings = self._protocol_warnings(
                    raw.get("warnings"),
                    page_level=True,
                    maximum=2,
                )
                pages.append(
                    OCRRuntimePage(
                        page_number=page_number,
                        text=text,
                        width_pixels=width_pixels,
                        height_pixels=height_pixels,
                        pixel_count=pixel_count,
                        png_sha256=png_sha256,
                        png_size_bytes=png_size_bytes,
                        warnings=page_warnings,
                    )
                )
            engine = self._protocol_text(
                payload,
                "engine",
                expected="tesseract",
                maximum=64,
            )
            renderer = self._protocol_text(
                payload,
                "renderer",
                expected="pdfium",
                maximum=64,
            )
            engine_version = self._protocol_text(
                payload,
                "engine_version",
                maximum=128,
            )
            renderer_version = self._protocol_text(
                payload,
                "renderer_version",
                maximum=128,
            )
            warnings = self._protocol_warnings(
                payload.get("warnings"),
                page_level=False,
                maximum=policy.max_selected_pages,
            )
            return OCRRuntimeResult(
                page_count=page_count,
                pages=tuple(pages),
                engine=engine,
                engine_version=engine_version,
                renderer=renderer,
                renderer_version=renderer_version,
                runtime_image=status.image,
                runtime_image_id=status.image_id,
                warnings=warnings,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime response schema is invalid.",
            ) from exc

    @staticmethod
    def _protocol_int(
        payload: dict[str, Any],
        key: str,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        value = payload[key]
        if type(value) is not int:
            raise TypeError
        if value < minimum:
            raise ValueError
        if maximum is not None and value > maximum:
            raise ValueError
        return value

    @staticmethod
    def _protocol_text(
        payload: dict[str, Any],
        key: str,
        *,
        maximum: int,
        expected: str | None = None,
    ) -> str:
        value = payload[key]
        if not isinstance(value, str):
            raise TypeError
        if not value or len(value) > maximum:
            raise ValueError
        if any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError
        if expected is not None and value != expected:
            raise ValueError
        return value

    @staticmethod
    def _protocol_warnings(
        value: Any,
        *,
        page_level: bool,
        maximum: int,
    ) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise TypeError
        if len(value) > maximum:
            raise ValueError
        warnings: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError
            valid = (
                item in _PAGE_WARNING_CODES
                if page_level
                else bool(_DOCUMENT_WARNING_RE.fullmatch(item))
            )
            if not valid:
                raise ValueError
            if item not in warnings:
                warnings.append(item)
        return tuple(warnings)

    @staticmethod
    def _safe_error_details(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key)
            if normalized not in _SAFE_ERROR_DETAIL_KEYS:
                continue
            if (
                type(child) is int
                and -(2**63) <= child < 2**63
            ):
                result[normalized] = child
        return result

    @staticmethod
    def _configured_enabled() -> bool:
        return (
            os.getenv("AI_STUDIO_OCR_ENABLED", "1")
            .strip()
            .casefold()
            in {"1", "true", "yes", "on"}
        )


@dataclass(frozen=True, slots=True)
class OCRPageResult:
    page_number: int
    text: str
    text_sha256: str
    character_count: int
    width_pixels: int
    height_pixels: int
    pixel_count: int
    png_sha256: str
    png_size_bytes: int
    engine: str
    engine_version: str
    renderer: str
    renderer_version: str
    runtime_image: str
    runtime_image_id: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_public_dict(
        self,
        *,
        include_text: bool = True,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "page_number": self.page_number,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "character_count": self.character_count,
            "width_pixels": self.width_pixels,
            "height_pixels": self.height_pixels,
            "pixel_count": self.pixel_count,
            "png_sha256": self.png_sha256,
            "png_size_bytes": self.png_size_bytes,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "renderer": self.renderer,
            "renderer_version": self.renderer_version,
            "runtime_image": self.runtime_image,
            "runtime_image_id": self.runtime_image_id,
            "warnings": list(self.warnings),
        }
        if not include_text:
            result.pop("text", None)
        return result


@dataclass(frozen=True, slots=True)
class DocumentOCRResult:
    document_format: DocumentFormat
    ocr_version: str
    source_sha256: str
    classification: str
    render_dpi: int
    page_count: int
    selected_page_count: int
    total_characters: int
    ocr_text_sha256: str
    pages: tuple[OCRPageResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_public_dict(
        self,
        *,
        include_text: bool = True,
    ) -> dict[str, Any]:
        return {
            "document_format": self.document_format.value,
            "ocr_version": self.ocr_version,
            "source_sha256": self.source_sha256,
            "classification": self.classification,
            "render_dpi": self.render_dpi,
            "page_count": self.page_count,
            "selected_page_count": self.selected_page_count,
            "total_characters": self.total_characters,
            "ocr_text_sha256": self.ocr_text_sha256,
            "warnings": list(self.warnings),
            "pages": [
                page.to_public_dict(
                    include_text=include_text
                )
                for page in self.pages
            ],
        }


class IsolatedPDFOCR:
    OCR_VERSION = "p3-001.4a-v1"

    def __init__(
        self,
        *,
        runtime: PDFOCRRuntime | None = None,
        policy: DocumentOCRPolicy | None = None,
        temp_root: Path | None = None,
    ) -> None:
        self._runtime = (
            runtime or DockerTesseractOCRRuntime()
        )
        self._policy = policy or DocumentOCRPolicy()
        configured_root = os.getenv(
            "AI_STUDIO_OCR_TEMP_ROOT",
            "",
        ).strip()
        root = (
            temp_root
            or (
                Path(configured_root).expanduser()
                if configured_root
                else (
                    Path(tempfile.gettempdir())
                    / "ai_studio_ocr"
                )
            )
        ).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._temp_root = root

    def recognize_pdf(
        self,
        *,
        descriptor: DocumentIntakeDescriptor,
        content: bytes,
        page_numbers: Iterable[int] | None = None,
    ) -> DocumentOCRResult:
        source_sha256 = hashlib.sha256(content).hexdigest()
        if source_sha256 != descriptor.content_sha256:
            raise DocumentOCRError(
                "DOCUMENT_OCR_SOURCE_HASH_MISMATCH",
                "Document bytes do not match the intake descriptor.",
            )
        if descriptor.document_format is not DocumentFormat.PDF:
            raise DocumentOCRError(
                "DOCUMENT_OCR_FORMAT_UNSUPPORTED",
                "OCR currently supports PDF documents only.",
            )

        selected = self._normalize_page_selection(
            page_numbers
        )

        with tempfile.TemporaryDirectory(
            prefix="document-ocr-",
            dir=str(self._temp_root),
        ) as directory:
            source_path = (
                Path(directory) / "document.pdf"
            )
            source_path.write_bytes(content)
            runtime_result = self._runtime.recognize_pdf(
                pdf_path=source_path,
                page_numbers=selected,
                policy=self._policy,
            )

        return self._validate_and_build(
            descriptor=descriptor,
            source_sha256=source_sha256,
            selected=selected,
            runtime_result=runtime_result,
        )

    def _validate_and_build(
        self,
        *,
        descriptor: DocumentIntakeDescriptor,
        source_sha256: str,
        selected: tuple[int, ...] | None,
        runtime_result: OCRRuntimeResult,
    ) -> DocumentOCRResult:
        page_count = runtime_result.page_count
        if page_count <= 0:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PDF_EMPTY",
                "PDF has no pages.",
            )
        if page_count > self._policy.max_pdf_pages:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PDF_PAGE_LIMIT",
                "PDF page count exceeds the configured OCR limit.",
                details={"page_count": page_count},
            )

        expected = (
            tuple(range(1, page_count + 1))
            if selected is None
            else selected
        )
        if len(expected) > self._policy.max_selected_pages:
            raise DocumentOCRError(
                "DOCUMENT_OCR_SELECTED_PAGE_LIMIT",
                "Selected OCR page count exceeds the configured limit.",
                details={
                    "selected_page_count": len(expected)
                },
            )
        invalid = [
            page_number
            for page_number in expected
            if (
                page_number < 1
                or page_number > page_count
            )
        ]
        if invalid:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_OUT_OF_RANGE",
                "OCR page selection is outside the PDF range.",
                details={"page_number": invalid[0]},
            )

        actual = tuple(
            page.page_number
            for page in runtime_result.pages
        )
        if actual != expected:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PAGE_MISMATCH",
                "OCR runtime returned an unexpected page set.",
            )

        pages: list[OCRPageResult] = []
        total_pixels = 0
        total_characters = 0
        runtime_document_warnings = (
            self._validated_runtime_warnings(
                runtime_result.warnings,
                page_level=False,
            )
        )
        document_warnings = list(runtime_document_warnings)
        blank_page_warnings: set[str] = set()

        for runtime_page in runtime_result.pages:
            self._validate_runtime_page(runtime_page)
            total_pixels += runtime_page.pixel_count
            if total_pixels > self._policy.max_total_pixels:
                raise DocumentOCRError(
                    "DOCUMENT_OCR_TOTAL_PIXEL_LIMIT",
                    "OCR render workload exceeds the total pixel limit.",
                    details={
                        "total_pixels": total_pixels
                    },
                )

            text = self._normalize_text(
                runtime_page.text
            )
            if (
                len(text)
                > self._policy
                .max_text_characters_per_page
            ):
                raise DocumentOCRError(
                    "DOCUMENT_OCR_PAGE_TEXT_LIMIT",
                    "OCR page text exceeds the configured limit.",
                    details={
                        "page_number": (
                            runtime_page.page_number
                        ),
                        "characters": len(text),
                    },
                )
            total_characters += len(text)
            if (
                total_characters
                > self._policy
                .max_total_text_characters
            ):
                raise DocumentOCRError(
                    "DOCUMENT_OCR_TOTAL_TEXT_LIMIT",
                    "OCR text exceeds the configured total limit.",
                    details={
                        "characters": total_characters
                    },
                )

            warnings = list(
                self._validated_runtime_warnings(
                    runtime_page.warnings,
                    page_level=True,
                )
            )
            if not text:
                if "OCR_PAGE_NO_TEXT" not in warnings:
                    warnings.append("OCR_PAGE_NO_TEXT")
                blank_warning = (
                    "OCR_PAGE_"
                    f"{runtime_page.page_number}_NO_TEXT"
                )
                blank_page_warnings.add(blank_warning)
                if blank_warning not in document_warnings:
                    document_warnings.append(blank_warning)
            elif "OCR_PAGE_NO_TEXT" in warnings:
                raise DocumentOCRError(
                    "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                    "OCR runtime returned inconsistent warnings.",
                )

            pages.append(
                OCRPageResult(
                    page_number=runtime_page.page_number,
                    text=text,
                    text_sha256=hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    character_count=len(text),
                    width_pixels=(
                        runtime_page.width_pixels
                    ),
                    height_pixels=(
                        runtime_page.height_pixels
                    ),
                    pixel_count=runtime_page.pixel_count,
                    png_sha256=runtime_page.png_sha256,
                    png_size_bytes=(
                        runtime_page.png_size_bytes
                    ),
                    engine=runtime_result.engine,
                    engine_version=(
                        runtime_result.engine_version
                    ),
                    renderer=runtime_result.renderer,
                    renderer_version=(
                        runtime_result.renderer_version
                    ),
                    runtime_image=(
                        runtime_result.runtime_image
                    ),
                    runtime_image_id=(
                        runtime_result.runtime_image_id
                    ),
                    warnings=tuple(warnings),
                )
            )

        if any(
            warning not in blank_page_warnings
            for warning in runtime_document_warnings
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned inconsistent warnings.",
            )

        canonical_text = "\n\n".join(
            page.text for page in pages
        )
        return DocumentOCRResult(
            document_format=descriptor.document_format,
            ocr_version=self.OCR_VERSION,
            source_sha256=source_sha256,
            classification=descriptor.classification.value,
            render_dpi=self._policy.render_dpi,
            page_count=page_count,
            selected_page_count=len(pages),
            total_characters=total_characters,
            ocr_text_sha256=hashlib.sha256(
                canonical_text.encode("utf-8")
            ).hexdigest(),
            pages=tuple(pages),
            warnings=tuple(document_warnings),
        )

    def _validate_runtime_page(
        self,
        page: OCRRuntimePage,
    ) -> None:
        if (
            page.page_number <= 0
            or page.width_pixels <= 0
            or page.height_pixels <= 0
            or page.pixel_count <= 0
            or page.png_size_bytes <= 0
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned invalid page metadata.",
            )
        if (
            page.width_pixels * page.height_pixels
            != page.pixel_count
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PIXEL_MISMATCH",
                "OCR runtime returned inconsistent pixel metadata.",
            )
        if (
            page.pixel_count
            > self._policy.max_page_pixels
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_PIXEL_LIMIT",
                "Rendered OCR page exceeds the pixel limit.",
                details={
                    "page_number": page.page_number,
                    "pixel_count": page.pixel_count,
                },
            )
        if (
            page.png_size_bytes
            > self._policy.max_png_bytes
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_IMAGE_LIMIT",
                "Rendered OCR page image exceeds the byte limit.",
                details={
                    "page_number": page.page_number,
                    "png_size_bytes": page.png_size_bytes,
                },
            )
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            page.png_sha256,
        ):
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned an invalid image hash.",
            )

    def _normalize_page_selection(
        self,
        page_numbers: Iterable[int] | None,
    ) -> tuple[int, ...] | None:
        if page_numbers is None:
            return None
        selected: set[int] = set()
        try:
            for value in page_numbers:
                if isinstance(value, bool):
                    raise ValueError
                page_number = int(value)
                if page_number != value:
                    raise ValueError
                selected.add(page_number)
        except (TypeError, ValueError) as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_SELECTION_INVALID",
                "OCR page selection is invalid.",
            ) from exc

        ordered = tuple(sorted(selected))
        if not ordered:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_SELECTION_EMPTY",
                "At least one OCR page must be selected.",
            )
        if len(ordered) > self._policy.max_selected_pages:
            raise DocumentOCRError(
                "DOCUMENT_OCR_SELECTED_PAGE_LIMIT",
                "Selected OCR page count exceeds the configured limit.",
                details={
                    "selected_page_count": len(ordered)
                },
            )
        if ordered[0] < 1:
            raise DocumentOCRError(
                "DOCUMENT_OCR_PAGE_OUT_OF_RANGE",
                "OCR page selection must use 1-based positive pages.",
                details={"page_number": ordered[0]},
            )
        return ordered

    def _validated_runtime_warnings(
        self,
        value: Iterable[str],
        *,
        page_level: bool,
    ) -> tuple[str, ...]:
        try:
            raw = tuple(value)
        except TypeError as exc:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned invalid warnings.",
            ) from exc
        maximum = (
            2
            if page_level
            else self._policy.max_selected_pages
        )
        if len(raw) > maximum:
            raise DocumentOCRError(
                "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                "OCR runtime returned invalid warnings.",
            )
        warnings: list[str] = []
        for item in raw:
            valid = (
                isinstance(item, str)
                and (
                    item in _PAGE_WARNING_CODES
                    if page_level
                    else bool(
                        _DOCUMENT_WARNING_RE.fullmatch(item)
                    )
                )
            )
            if not valid:
                raise DocumentOCRError(
                    "DOCUMENT_OCR_RUNTIME_PROTOCOL_INVALID",
                    "OCR runtime returned invalid warnings.",
                )
            if item not in warnings:
                warnings.append(item)
        return tuple(warnings)

    @staticmethod
    def _normalize_text(value: str) -> str:
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
