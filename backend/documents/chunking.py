from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from backend.documents.extraction import (
    DocumentExtractionResult,
    ExtractedTextUnit,
)


class DocumentChunkingError(RuntimeError):
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
class DocumentChunkingPolicy:
    max_chunk_characters: int = 4_000
    overlap_characters: int = 400
    max_chunks: int = 20_000

    def __post_init__(self) -> None:
        if self.max_chunk_characters <= 0:
            raise ValueError(
                "max_chunk_characters must be positive"
            )
        if self.overlap_characters < 0:
            raise ValueError(
                "overlap_characters must be non-negative"
            )
        if (
            self.overlap_characters
            >= self.max_chunk_characters
        ):
            raise ValueError(
                "overlap_characters must be smaller than "
                "max_chunk_characters"
            )
        if self.max_chunks <= 0:
            raise ValueError("max_chunks must be positive")


@dataclass(frozen=True, slots=True)
class DocumentTextChunk:
    ordinal: int
    text: str
    text_sha256: str
    character_start: int
    character_end: int
    source_unit_ordinals: tuple[int, ...]
    provenance: dict[str, Any] = field(
        default_factory=dict
    )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "character_start": self.character_start,
            "character_end": self.character_end,
            "source_unit_ordinals": list(
                self.source_unit_ordinals
            ),
            "provenance": dict(self.provenance),
        }


class DeterministicDocumentChunker:
    CHUNKER_VERSION = "p3-001.3b-v1"

    def __init__(
        self,
        policy: DocumentChunkingPolicy | None = None,
    ) -> None:
        self._policy = policy or DocumentChunkingPolicy()

    def chunk(
        self,
        extraction: DocumentExtractionResult,
    ) -> tuple[DocumentTextChunk, ...]:
        canonical_text, spans = self._canonical_text_and_spans(
            extraction.units
        )
        calculated_hash = hashlib.sha256(
            canonical_text.encode("utf-8")
        ).hexdigest()
        if (
            calculated_hash
            != extraction.extracted_text_sha256
        ):
            raise DocumentChunkingError(
                "DOCUMENT_CHUNKING_EXTRACTION_HASH_MISMATCH",
                "Canonical extracted text hash is inconsistent.",
            )

        if not canonical_text:
            return ()

        step = (
            self._policy.max_chunk_characters
            - self._policy.overlap_characters
        )
        chunks: list[DocumentTextChunk] = []
        start = 0

        while start < len(canonical_text):
            if len(chunks) >= self._policy.max_chunks:
                raise DocumentChunkingError(
                    "DOCUMENT_CHUNKING_CHUNK_LIMIT",
                    "Chunk count exceeds the configured limit.",
                )

            end = min(
                start + self._policy.max_chunk_characters,
                len(canonical_text),
            )
            text = canonical_text[start:end]
            source_ordinals = tuple(
                ordinal
                for ordinal, unit_start, unit_end
                in spans
                if (
                    unit_end > start
                    and unit_start < end
                )
            )
            chunks.append(
                DocumentTextChunk(
                    ordinal=len(chunks),
                    text=text,
                    text_sha256=hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    character_start=start,
                    character_end=end,
                    source_unit_ordinals=source_ordinals,
                    provenance={
                        "chunker_version": (
                            self.CHUNKER_VERSION
                        ),
                        "document_character_start": start,
                        "document_character_end": end,
                        "source_unit_ordinals": list(
                            source_ordinals
                        ),
                    },
                )
            )

            if end >= len(canonical_text):
                break
            start += step

        return tuple(chunks)

    @staticmethod
    def _canonical_text_and_spans(
        units: tuple[ExtractedTextUnit, ...],
    ) -> tuple[
        str,
        list[tuple[int, int, int]],
    ]:
        parts: list[str] = []
        spans: list[tuple[int, int, int]] = []
        cursor = 0

        for index, unit in enumerate(units):
            if index:
                parts.append("\n\n")
                cursor += 2

            unit_start = cursor
            parts.append(unit.text)
            cursor += len(unit.text)
            spans.append(
                (
                    unit.ordinal,
                    unit_start,
                    cursor,
                )
            )

        return "".join(parts), spans
