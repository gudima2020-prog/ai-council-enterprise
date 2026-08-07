from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping


AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION = "p3-002.1a.context-checkpoint"

_CHECKPOINT_MAX_BYTES = 32_768
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/+-]*$")
_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_BRANCH_PATTERN = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9._/-]*[a-zA-Z0-9])?$"
)
_GIT_SHA_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[a-zA-Z]:")

_CHECKPOINT_FIELDS = {
    "schema_version",
    "objective",
    "confirmed_facts",
    "changed_files",
    "test_results",
    "open_questions",
    "blocked_actions",
    "branch",
    "current_commit",
    "migration_head",
    "next_safe_action",
    "workspace_id",
    "profile_id",
    "profile_fingerprint",
}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Context checkpoint must be JSON-serializable."
        ) from exc


def _normalize_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required.")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field_name} is required.")
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(
            f"{field_name} exceeds the {max_length}-character limit."
        )
    if any(
        ord(character) < 32
        and character not in {"\n", "\r", "\t"}
        for character in normalized
    ):
        raise ValueError(
            f"{field_name} contains unsupported control characters."
        )
    return normalized


def _normalize_text_items(
    values: Iterable[str],
    *,
    field_name: str,
    max_items: int = 128,
    max_item_length: int = 1_000,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError(f"{field_name} must be an array.")
    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an array.") from exc
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = _normalize_text(
            raw,
            field_name=field_name,
            max_length=max_item_length,
            required=True,
        )
        assert item is not None
        if item not in seen:
            result.append(item)
            seen.add(item)
    if len(result) > max_items:
        raise ValueError(
            f"{field_name} supports at most {max_items} entries."
        )
    return tuple(result)


def _normalize_changed_files(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError("changed_files must be an array.")
    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise ValueError("changed_files must be an array.") from exc
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            raise ValueError("changed_files must contain strings.")
        normalized = raw.strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or _WINDOWS_DRIVE_PATTERN.match(normalized)
            or ":" in normalized
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError(
                "changed_files must contain repository-relative paths."
            )
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(
                "changed_files must contain repository-relative paths "
                "without traversal."
            )
        value = path.as_posix()
        if len(value) > 512:
            raise ValueError(
                "changed_files path exceeds the 512-character limit."
            )
        if value not in seen:
            result.append(value)
            seen.add(value)
    if len(result) > 512:
        raise ValueError("changed_files supports at most 512 entries.")
    return tuple(result)


def _normalize_identifier(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    normalized = _normalize_text(
        value,
        field_name=field_name,
        max_length=max_length,
    )
    if normalized is None:
        return None
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} contains unsupported characters."
        )
    return normalized


def _normalize_profile_id(value: str | None) -> str | None:
    normalized = _normalize_text(
        value,
        field_name="profile_id",
        max_length=64,
    )
    if normalized is None:
        return None
    normalized = normalized.lower()
    if not _PROFILE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("profile_id contains unsupported characters.")
    return normalized


def _normalize_branch(value: str | None) -> str | None:
    normalized = _normalize_text(
        value,
        field_name="branch",
        max_length=255,
    )
    if normalized is None:
        return None
    parts = normalized.split("/")
    if (
        not _BRANCH_PATTERN.fullmatch(normalized)
        or ".." in normalized
        or "@{" in normalized
        or "//" in normalized
        or any(
            part.startswith(".") or part.endswith(".lock")
            for part in parts
        )
    ):
        raise ValueError("branch is not a safe Git ref name.")
    return normalized


def _normalize_sha256(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    normalized = _normalize_text(
        value,
        field_name=field_name,
        max_length=64,
    )
    if normalized is None:
        return None
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must be a 64-character SHA-256 hex digest."
        )
    return normalized.lower()


class AgentContextCheckpointIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class AgentContextCheckpoint:
    """Bounded, content-free state for safe session continuation.

    Callers must store only task metadata and summaries. Raw prompts, document
    content, model responses, secrets, tokens and personal data do not belong in
    this contract. Persistence and retention are delivered in a later slice.
    """

    objective: str
    next_safe_action: str
    confirmed_facts: tuple[str, ...] = field(default_factory=tuple)
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    test_results: tuple[str, ...] = field(default_factory=tuple)
    open_questions: tuple[str, ...] = field(default_factory=tuple)
    blocked_actions: tuple[str, ...] = field(default_factory=tuple)
    branch: str | None = None
    current_commit: str | None = None
    migration_head: str | None = None
    workspace_id: str | None = None
    profile_id: str | None = None
    profile_fingerprint: str | None = None

    def __post_init__(self) -> None:
        objective = _normalize_text(
            self.objective,
            field_name="objective",
            max_length=2_000,
            required=True,
        )
        next_safe_action = _normalize_text(
            self.next_safe_action,
            field_name="next_safe_action",
            max_length=2_000,
            required=True,
        )
        confirmed_facts = _normalize_text_items(
            self.confirmed_facts,
            field_name="confirmed_facts",
        )
        changed_files = _normalize_changed_files(self.changed_files)
        test_results = _normalize_text_items(
            self.test_results,
            field_name="test_results",
        )
        open_questions = _normalize_text_items(
            self.open_questions,
            field_name="open_questions",
        )
        blocked_actions = _normalize_text_items(
            self.blocked_actions,
            field_name="blocked_actions",
            max_item_length=255,
        )
        branch = _normalize_branch(self.branch)
        current_commit = _normalize_text(
            self.current_commit,
            field_name="current_commit",
            max_length=64,
        )
        if (
            current_commit is not None
            and not _GIT_SHA_PATTERN.fullmatch(current_commit)
        ):
            raise ValueError(
                "current_commit must be a full 40- or 64-character Git SHA."
            )
        current_commit = (
            None
            if current_commit is None
            else current_commit.lower()
        )
        migration_head = _normalize_identifier(
            self.migration_head,
            field_name="migration_head",
            max_length=128,
        )
        workspace_id = _normalize_identifier(
            self.workspace_id,
            field_name="workspace_id",
            max_length=64,
        )
        profile_id = _normalize_profile_id(self.profile_id)
        profile_fingerprint = _normalize_sha256(
            self.profile_fingerprint,
            field_name="profile_fingerprint",
        )
        if profile_id is None and profile_fingerprint is not None:
            raise ValueError(
                "profile_id is required when profile_fingerprint is set."
            )
        if profile_id is not None and profile_fingerprint is None:
            raise ValueError(
                "profile_fingerprint is required when profile_id is set."
            )

        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "next_safe_action", next_safe_action)
        object.__setattr__(self, "confirmed_facts", confirmed_facts)
        object.__setattr__(self, "changed_files", changed_files)
        object.__setattr__(self, "test_results", test_results)
        object.__setattr__(self, "open_questions", open_questions)
        object.__setattr__(self, "blocked_actions", blocked_actions)
        object.__setattr__(self, "branch", branch)
        object.__setattr__(self, "current_commit", current_commit)
        object.__setattr__(self, "migration_head", migration_head)
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(
            self,
            "profile_fingerprint",
            profile_fingerprint,
        )

        canonical = _canonical_json(self.to_dict())
        if len(canonical.encode("utf-8")) > _CHECKPOINT_MAX_BYTES:
            raise ValueError(
                "Context checkpoint exceeds the 32768-byte canonical limit."
            )

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_json(self.to_dict())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION,
            "objective": self.objective,
            "confirmed_facts": list(self.confirmed_facts),
            "changed_files": list(self.changed_files),
            "test_results": list(self.test_results),
            "open_questions": list(self.open_questions),
            "blocked_actions": list(self.blocked_actions),
            "branch": self.branch,
            "current_commit": self.current_commit,
            "migration_head": self.migration_head,
            "next_safe_action": self.next_safe_action,
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
        }

    def to_envelope(self) -> dict[str, Any]:
        return {
            **self.to_dict(),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> AgentContextCheckpoint:
        values = dict(payload)
        unknown = sorted(set(values) - _CHECKPOINT_FIELDS)
        if unknown:
            raise ValueError(
                "Unsupported checkpoint fields: " + ", ".join(unknown)
            )
        schema_version = values.pop("schema_version", None)
        if schema_version != AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported context checkpoint schema_version: "
                f"{schema_version!r}."
            )
        try:
            return cls(**values)
        except TypeError as exc:
            raise ValueError(
                "Context checkpoint fields are incomplete or invalid."
            ) from exc

    @classmethod
    def from_envelope(
        cls,
        payload: Mapping[str, Any],
    ) -> AgentContextCheckpoint:
        values = dict(payload)
        supplied_fingerprint = values.pop("fingerprint", None)
        try:
            normalized_fingerprint = _normalize_sha256(
                supplied_fingerprint,
                field_name="fingerprint",
            )
        except ValueError as exc:
            raise AgentContextCheckpointIntegrityError(
                "Checkpoint fingerprint is invalid."
            ) from exc
        if normalized_fingerprint is None:
            raise AgentContextCheckpointIntegrityError(
                "Checkpoint fingerprint is required."
            )
        checkpoint = cls.from_dict(values)
        if not hmac.compare_digest(
            checkpoint.fingerprint,
            normalized_fingerprint,
        ):
            raise AgentContextCheckpointIntegrityError(
                "Checkpoint fingerprint does not match its content."
            )
        return checkpoint
