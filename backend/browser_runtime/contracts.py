from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from backend.secrets.references import SecretReferenceError, parse_secret_reference
from backend.task_engine.governance import ExecutionInitiator


BROWSER_CONTRACT_SCHEMA_VERSION = "arch-browser-contract-001.v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z._+-]{0,47}$")
_SECRET_PROVIDER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")
_OBVIOUS_SECRET_LITERALS = frozenset(
    {"hunter2", "password", "passwd", "secret", "changeme", "letmein"}
)
_SECRET_VALUE_PREFIXES = (
    "authorization:",
    "cookie:",
    "set-cookie:",
    "sessionid:",
    "approval-token-",
    "sk-proj-",
    "sk-live-",
    "sk_test_",
    "sk_live_",
    "ghp_",
    "github_pat_",
    "xoxb-",
    "xoxp-",
    "akia",
    "-----begin ",
)


class BrowserContractError(ValueError):
    pass


class BrowserScriptRuntimeKind(StrEnum):
    PYTHON_PLAYWRIGHT = "python-playwright"


class BrowserEffect(StrEnum):
    BROWSER_READ = "browser_read"
    BROWSER_EXTERNAL_WRITE = "browser_external_write"
    BROWSER_DOWNLOAD = "browser_download"
    BROWSER_UPLOAD = "browser_upload"
    BROWSER_SECRET_USE = "browser_secret_use"


class BrowserNetworkMode(StrEnum):
    NONE = "none"
    FIXTURE_ONLY = "fixture_only"
    RESTRICTED_EXTERNAL = "restricted_external"


class BrowserFilesystemMode(StrEnum):
    WORKSPACE_ONLY = "workspace_only"
    READ_ONLY_WORKSPACE = "read_only_workspace"


class BrowserDownloadPolicy(StrEnum):
    DENY = "deny"
    WORKSPACE_ONLY = "workspace_only"


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class BrowserTerminalResult(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class EvidenceLocationKind(StrEnum):
    RELATIVE_PATH = "relative_path"
    LOGICAL_KEY = "logical_key"


class EvidenceRedactionState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    REDACTED = "redacted"
    UNREDACTED_NON_SENSITIVE = "unredacted_non_sensitive"


class EvidencePrivacyClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize_json(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_payload(domain: str, value: Any) -> str:
    domain_token = _token(domain, "fingerprint_domain")
    material = domain_token.encode() + b"\0" + canonical_json(value).encode()
    return hashlib.sha256(material).hexdigest()


def fingerprint_input(value: Any) -> str:
    return fingerprint_payload("browser-input-v1", value)


def sha256_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise BrowserContractError("sha256_bytes requires bytes.")
    return hashlib.sha256(value).hexdigest()


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise BrowserContractError("Non-finite JSON numbers are not allowed.")
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return _dt(value, "datetime").isoformat()
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise BrowserContractError("Canonical JSON object keys must be strings.")
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    raise BrowserContractError(
        f"Unsupported canonical JSON value type: {type(value).__name__}."
    )


def _schema(value: str) -> str:
    if value != BROWSER_CONTRACT_SCHEMA_VERSION:
        raise BrowserContractError(f"Unsupported schema_version: {value!r}.")
    return value


def _reject_secret_like(value: str, field_name: str) -> None:
    lowered = value.strip().lower()
    if lowered in _OBVIOUS_SECRET_LITERALS:
        raise BrowserContractError(
            f"{field_name} must not contain secret material."
        )
    if any(lowered.startswith(prefix) for prefix in _SECRET_VALUE_PREFIXES):
        raise BrowserContractError(
            f"{field_name} must not contain secret material."
        )
    compact = value.strip()
    if len(compact) >= 48 and re.fullmatch(r"[0-9a-fA-F]+", compact):
        raise BrowserContractError(
            f"{field_name} must not contain key-like hex material."
        )


def _token(value: str, field_name: str, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise BrowserContractError(f"{field_name} must be a string.")
    _reject_secret_like(value, field_name)
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > max_length
        or not _TOKEN_RE.fullmatch(normalized)
    ):
        raise BrowserContractError(f"Invalid {field_name}: {value!r}.")
    return normalized


def _optional_token(value: str | None, field_name: str) -> str | None:
    return None if value is None else _token(value, field_name)


def _sha(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.strip()):
        raise BrowserContractError(
            f"{field_name} must be a 64-character SHA-256 hex digest."
        )
    return value.strip().lower()


def _tokens(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise BrowserContractError(f"{field_name} must be an array.")
    return tuple(sorted({_token(item, field_name) for item in values}))


def _enum(value: Any, enum_type: type[StrEnum], field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise BrowserContractError(f"Unsupported {field_name}: {value!r}.")
    try:
        return enum_type(value.strip().lower())
    except ValueError as exc:
        raise BrowserContractError(f"Unsupported {field_name}: {value!r}.") from exc


def _enums(
    values: Iterable[Any],
    enum_type: type[StrEnum],
    field_name: str,
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise BrowserContractError(f"{field_name} must be an array.")
    selected = {_enum(item, enum_type, field_name) for item in values}
    return tuple(item for item in enum_type if item in selected)


def _credential_scope(value: str) -> str:
    if not isinstance(value, str):
        raise BrowserContractError("credential scope must be a string.")
    reference = value.strip()
    try:
        provider_key, secret_key = parse_secret_reference(reference)
    except SecretReferenceError as exc:
        raise BrowserContractError(
            "credential scopes must use secret://provider/key references."
        ) from exc
    if not _SECRET_PROVIDER_RE.fullmatch(provider_key):
        raise BrowserContractError("Invalid credential scope provider key.")
    if not _SECRET_KEY_RE.fullmatch(secret_key):
        raise BrowserContractError("Invalid credential scope secret key.")
    if any(part in {"", ".", ".."} for part in secret_key.split("/")):
        raise BrowserContractError("Invalid credential scope path.")
    return f"secret://{provider_key.lower()}/{secret_key}"


def _credential_scopes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise BrowserContractError("credential_scopes must be an array.")
    return tuple(sorted({_credential_scope(item) for item in values}))


def _version(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise BrowserContractError(f"{field_name} must be a string.")
    normalized = value.strip()
    _reject_secret_like(normalized, field_name)
    if not _VERSION_RE.fullmatch(normalized):
        raise BrowserContractError(
            f"{field_name} must use a bounded version identifier grammar."
        )
    return normalized


def _dt(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise BrowserContractError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise BrowserContractError(f"{field_name} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _origin(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrowserContractError("navigation origin must be a non-empty string.")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserContractError("navigation origin must be an http(s) origin.")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserContractError("navigation origin cannot contain credentials.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise BrowserContractError(
            "navigation origin cannot contain path, query, or fragment."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserContractError("Invalid navigation origin port.") from exc
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = "" if port in {None, default_port} else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _origins(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise BrowserContractError("requested_navigation_origins must be an array.")
    return tuple(sorted({_origin(item) for item in values}))


def _relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise BrowserContractError("Evidence path must be a string.")
    normalized = value.strip().replace("\\", "/")
    if not normalized or "\0" in normalized or normalized.startswith("/"):
        raise BrowserContractError("Evidence path must be a non-empty relative path.")
    if re.match(r"^[A-Za-z]:", normalized) or ":" in normalized:
        raise BrowserContractError("Windows drive/ADS forms are not allowed.")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BrowserContractError("Evidence path must be canonical and traversal-free.")
    for part in parts:
        _reject_secret_like(part, "path_segment")
    path = PurePosixPath(*parts)
    if path.is_absolute():
        raise BrowserContractError("Evidence path must be relative.")
    return path.as_posix()


def _safe_text(value: str, field_name: str, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise BrowserContractError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length or "\0" in normalized:
        raise BrowserContractError(f"Invalid {field_name}.")
    return normalized


@dataclass(frozen=True)
class BrowserProvenanceRef:
    source_kind: str
    source_id: str
    source_revision: str | None = None
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _token(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_id", _token(self.source_id, "source_id"))
        object.__setattr__(
            self,
            "source_revision",
            _optional_token(self.source_revision, "source_revision"),
        )
        if self.source_fingerprint is not None:
            object.__setattr__(
                self,
                "source_fingerprint",
                _sha(self.source_fingerprint, "source_fingerprint"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True)
class BrowserScriptArtifact:
    artifact_id: str
    revision_id: str
    source_sha256: str
    runtime_kind: BrowserScriptRuntimeKind | str = (
        BrowserScriptRuntimeKind.PYTHON_PLAYWRIGHT
    )
    entrypoint: str | None = None
    provenance: tuple[BrowserProvenanceRef, ...] = field(default_factory=tuple)
    schema_version: str = BROWSER_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        object.__setattr__(self, "artifact_id", _token(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "revision_id", _token(self.revision_id, "revision_id"))
        object.__setattr__(
            self, "source_sha256", _sha(self.source_sha256, "source_sha256")
        )
        object.__setattr__(
            self,
            "runtime_kind",
            _enum(self.runtime_kind, BrowserScriptRuntimeKind, "runtime_kind"),
        )
        if self.entrypoint is not None:
            object.__setattr__(self, "entrypoint", _relative_path(self.entrypoint))
        provenance = tuple(self.provenance)
        if any(not isinstance(item, BrowserProvenanceRef) for item in provenance):
            raise BrowserContractError(
                "provenance must contain BrowserProvenanceRef items."
            )
        object.__setattr__(
            self,
            "provenance",
            tuple(
                sorted(
                    provenance,
                    key=lambda item: (
                        item.source_kind,
                        item.source_id,
                        item.source_revision or "",
                        item.source_fingerprint or "",
                    ),
                )
            ),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "revision_id": self.revision_id,
            "runtime_kind": self.runtime_kind.value,
            "source_sha256": self.source_sha256,
            "entrypoint": self.entrypoint,
            "provenance": [item.to_dict() for item in self.provenance],
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload("browser-script-artifact-v1", self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class BrowserRuntimeRequirements:
    ephemeral_runtime_required: bool = True
    fresh_browser_session_required: bool = True
    browser_engine: BrowserEngine | str = BrowserEngine.CHROMIUM
    filesystem_mode: BrowserFilesystemMode | str = BrowserFilesystemMode.WORKSPACE_ONLY
    network_mode: BrowserNetworkMode | str = BrowserNetworkMode.NONE
    download_policy: BrowserDownloadPolicy | str = BrowserDownloadPolicy.DENY
    max_runtime_seconds: int = 120
    max_memory_mb: int = 1024
    max_pids: int = 128
    capture_screenshots: bool = True
    credential_scopes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = BROWSER_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        if self.ephemeral_runtime_required is not True:
            raise BrowserContractError("Browser runtime must be ephemeral.")
        if self.fresh_browser_session_required is not True:
            raise BrowserContractError("Browser execution requires a fresh session.")
        object.__setattr__(
            self,
            "browser_engine",
            _enum(self.browser_engine, BrowserEngine, "browser_engine"),
        )
        object.__setattr__(
            self,
            "filesystem_mode",
            _enum(self.filesystem_mode, BrowserFilesystemMode, "filesystem_mode"),
        )
        object.__setattr__(
            self,
            "network_mode",
            _enum(self.network_mode, BrowserNetworkMode, "network_mode"),
        )
        object.__setattr__(
            self,
            "download_policy",
            _enum(self.download_policy, BrowserDownloadPolicy, "download_policy"),
        )
        if (
            not isinstance(self.max_runtime_seconds, int)
            or isinstance(self.max_runtime_seconds, bool)
            or not 1 <= self.max_runtime_seconds <= 3600
        ):
            raise BrowserContractError("max_runtime_seconds must be 1..3600.")
        if (
            not isinstance(self.max_memory_mb, int)
            or isinstance(self.max_memory_mb, bool)
            or not 128 <= self.max_memory_mb <= 16384
        ):
            raise BrowserContractError("max_memory_mb must be 128..16384.")
        if (
            not isinstance(self.max_pids, int)
            or isinstance(self.max_pids, bool)
            or not 16 <= self.max_pids <= 2048
        ):
            raise BrowserContractError("max_pids must be 16..2048.")
        if not isinstance(self.capture_screenshots, bool):
            raise BrowserContractError("capture_screenshots must be boolean.")
        object.__setattr__(
            self,
            "credential_scopes",
            _credential_scopes(self.credential_scopes),
        )
        if (
            self.filesystem_mode is BrowserFilesystemMode.READ_ONLY_WORKSPACE
            and self.download_policy is BrowserDownloadPolicy.WORKSPACE_ONLY
        ):
            raise BrowserContractError(
                "workspace downloads require a writable workspace."
            )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ephemeral_runtime_required": self.ephemeral_runtime_required,
            "fresh_browser_session_required": self.fresh_browser_session_required,
            "browser_engine": self.browser_engine.value,
            "filesystem_mode": self.filesystem_mode.value,
            "network_mode": self.network_mode.value,
            "download_policy": self.download_policy.value,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_memory_mb": self.max_memory_mb,
            "max_pids": self.max_pids,
            "capture_screenshots": self.capture_screenshots,
            "credential_scopes": list(self.credential_scopes),
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload("browser-runtime-requirements-v1", self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class BrowserExecutionSpec:
    task_id: str
    revision_id: str
    execution_initiator: ExecutionInitiator | str
    script_artifact_fingerprint: str
    input_fingerprint: str
    runtime_requirements: BrowserRuntimeRequirements
    workspace_id: str | None = None
    requested_capabilities: tuple[str, ...] = field(default_factory=tuple)
    effects: tuple[BrowserEffect | str, ...] = (BrowserEffect.BROWSER_READ,)
    requested_navigation_origins: tuple[str, ...] = field(default_factory=tuple)
    requested_evidence: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = BROWSER_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        object.__setattr__(self, "task_id", _token(self.task_id, "task_id"))
        object.__setattr__(self, "revision_id", _token(self.revision_id, "revision_id"))
        object.__setattr__(
            self,
            "workspace_id",
            _optional_token(self.workspace_id, "workspace_id"),
        )
        try:
            initiator = (
                self.execution_initiator
                if isinstance(self.execution_initiator, ExecutionInitiator)
                else ExecutionInitiator(str(self.execution_initiator).strip().lower())
            )
        except ValueError as exc:
            raise BrowserContractError("Unsupported execution_initiator.") from exc
        object.__setattr__(self, "execution_initiator", initiator)
        object.__setattr__(
            self,
            "script_artifact_fingerprint",
            _sha(self.script_artifact_fingerprint, "script_artifact_fingerprint"),
        )
        object.__setattr__(
            self,
            "input_fingerprint",
            _sha(self.input_fingerprint, "input_fingerprint"),
        )
        if not isinstance(self.runtime_requirements, BrowserRuntimeRequirements):
            raise BrowserContractError(
                "runtime_requirements must be BrowserRuntimeRequirements."
            )
        object.__setattr__(
            self,
            "requested_capabilities",
            _tokens(self.requested_capabilities, "requested_capabilities"),
        )
        effects = _enums(self.effects, BrowserEffect, "effects")
        if not effects:
            raise BrowserContractError("At least one browser effect is required.")
        object.__setattr__(self, "effects", effects)
        origins = _origins(self.requested_navigation_origins)
        object.__setattr__(self, "requested_navigation_origins", origins)
        object.__setattr__(
            self,
            "requested_evidence",
            _tokens(self.requested_evidence, "requested_evidence"),
        )

        req = self.runtime_requirements
        if req.network_mode in {
            BrowserNetworkMode.NONE,
            BrowserNetworkMode.FIXTURE_ONLY,
        } and origins:
            raise BrowserContractError(
                "none/fixture_only network modes cannot request navigation origins."
            )
        if req.network_mode is BrowserNetworkMode.RESTRICTED_EXTERNAL and not origins:
            raise BrowserContractError(
                "restricted_external requires requested navigation origins."
            )
        if (
            BrowserEffect.BROWSER_EXTERNAL_WRITE in effects
            and req.network_mode is not BrowserNetworkMode.RESTRICTED_EXTERNAL
        ):
            raise BrowserContractError(
                "browser_external_write requires restricted_external network mode."
            )
        if (
            BrowserEffect.BROWSER_UPLOAD in effects
            and req.network_mode is BrowserNetworkMode.NONE
        ):
            raise BrowserContractError(
                "browser_upload requires fixture_only or restricted_external network mode."
            )
        if (
            BrowserEffect.BROWSER_DOWNLOAD in effects
            and req.download_policy is BrowserDownloadPolicy.DENY
        ):
            raise BrowserContractError(
                "browser_download contradicts download_policy=deny."
            )
        if (
            BrowserEffect.BROWSER_SECRET_USE in effects
            and not req.credential_scopes
        ):
            raise BrowserContractError(
                "browser_secret_use requires credential scopes."
            )
        if req.credential_scopes and BrowserEffect.BROWSER_SECRET_USE not in effects:
            raise BrowserContractError(
                "credential scopes require browser_secret_use effect."
            )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "revision_id": self.revision_id,
            "workspace_id": self.workspace_id,
            "execution_initiator": self.execution_initiator.value,
            "script_artifact_fingerprint": self.script_artifact_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "requested_capabilities": list(self.requested_capabilities),
            "effects": [item.value for item in self.effects],
            "requested_navigation_origins": list(self.requested_navigation_origins),
            "requested_evidence": list(self.requested_evidence),
            "runtime_requirements": self.runtime_requirements.to_dict(),
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload("browser-execution-spec-v1", self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class BrowserRuntimeAttestation:
    task_id: str
    revision_id: str
    run_id: str
    script_artifact_fingerprint: str
    input_fingerprint: str
    runtime_provider: str
    browser_engine: BrowserEngine | str
    started_at: datetime
    finished_at: datetime
    terminal_result: BrowserTerminalResult | str
    workspace_id: str | None = None
    runtime_image_digest: str | None = None
    browser_version: str | None = None
    automation_runtime_version: str | None = None
    network_policy_fingerprint: str | None = None
    governance_policy_fingerprints: tuple[str, ...] = field(default_factory=tuple)
    failure_class: str | None = None
    schema_version: str = BROWSER_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in ("task_id", "revision_id", "run_id"):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        object.__setattr__(
            self,
            "workspace_id",
            _optional_token(self.workspace_id, "workspace_id"),
        )
        for name in ("script_artifact_fingerprint", "input_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        object.__setattr__(
            self,
            "runtime_provider",
            _token(self.runtime_provider, "runtime_provider"),
        )
        object.__setattr__(
            self,
            "browser_engine",
            _enum(self.browser_engine, BrowserEngine, "browser_engine"),
        )
        started = _dt(self.started_at, "started_at")
        finished = _dt(self.finished_at, "finished_at")
        if finished < started:
            raise BrowserContractError("finished_at cannot precede started_at.")
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)
        result = _enum(
            self.terminal_result,
            BrowserTerminalResult,
            "terminal_result",
        )
        object.__setattr__(self, "terminal_result", result)

        if self.runtime_image_digest is not None:
            raw = self.runtime_image_digest.strip().lower()
            digest = raw[7:] if raw.startswith("sha256:") else raw
            object.__setattr__(
                self,
                "runtime_image_digest",
                "sha256:" + _sha(digest, "runtime_image_digest"),
            )
        for name in ("browser_version", "automation_runtime_version"):
            if getattr(self, name) is not None:
                object.__setattr__(
                    self,
                    name,
                    _version(getattr(self, name), name),
                )
        if self.network_policy_fingerprint is not None:
            object.__setattr__(
                self,
                "network_policy_fingerprint",
                _sha(
                    self.network_policy_fingerprint,
                    "network_policy_fingerprint",
                ),
            )
        object.__setattr__(
            self,
            "governance_policy_fingerprints",
            tuple(
                sorted(
                    {
                        _sha(item, "governance_policy_fingerprint")
                        for item in self.governance_policy_fingerprints
                    }
                )
            ),
        )
        if self.failure_class is not None:
            object.__setattr__(
                self,
                "failure_class",
                _token(self.failure_class, "failure_class"),
            )
        if result is BrowserTerminalResult.COMPLETED and self.failure_class:
            raise BrowserContractError(
                "Completed attestation cannot declare failure_class."
            )
        if result is not BrowserTerminalResult.COMPLETED and not self.failure_class:
            raise BrowserContractError(
                "Non-completed attestation requires failure_class."
            )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "revision_id": self.revision_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "script_artifact_fingerprint": self.script_artifact_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "runtime_provider": self.runtime_provider,
            "runtime_image_digest": self.runtime_image_digest,
            "browser_engine": self.browser_engine.value,
            "browser_version": self.browser_version,
            "automation_runtime_version": self.automation_runtime_version,
            "network_policy_fingerprint": self.network_policy_fingerprint,
            "governance_policy_fingerprints": list(
                self.governance_policy_fingerprints
            ),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "terminal_result": self.terminal_result.value,
            "failure_class": self.failure_class,
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload("browser-runtime-attestation-v1", self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class BrowserEvidenceItem:
    evidence_id: str
    evidence_type: str
    location_kind: EvidenceLocationKind | str
    location: str
    sha256: str
    size_bytes: int
    redaction_state: EvidenceRedactionState | str
    privacy_classification: EvidencePrivacyClass | str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_id", _token(self.evidence_id, "evidence_id")
        )
        object.__setattr__(
            self, "evidence_type", _token(self.evidence_type, "evidence_type")
        )
        kind = _enum(self.location_kind, EvidenceLocationKind, "location_kind")
        object.__setattr__(self, "location_kind", kind)
        location = (
            _relative_path(self.location)
            if kind is EvidenceLocationKind.RELATIVE_PATH
            else _token(self.location, "logical_key")
        )
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "sha256", _sha(self.sha256, "evidence_sha256"))
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise BrowserContractError("size_bytes must be non-negative.")
        object.__setattr__(
            self,
            "redaction_state",
            _enum(self.redaction_state, EvidenceRedactionState, "redaction_state"),
        )
        object.__setattr__(
            self,
            "privacy_classification",
            _enum(
                self.privacy_classification,
                EvidencePrivacyClass,
                "privacy_classification",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "location_kind": self.location_kind.value,
            "location": self.location,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "redaction_state": self.redaction_state.value,
            "privacy_classification": self.privacy_classification.value,
        }


@dataclass(frozen=True)
class BrowserEvidenceManifest:
    run_id: str
    task_id: str
    revision_id: str
    script_artifact_fingerprint: str
    input_fingerprint: str
    items: tuple[BrowserEvidenceItem, ...]
    workspace_id: str | None = None
    schema_version: str = BROWSER_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in ("run_id", "task_id", "revision_id"):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        object.__setattr__(
            self,
            "workspace_id",
            _optional_token(self.workspace_id, "workspace_id"),
        )
        for name in ("script_artifact_fingerprint", "input_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        items = tuple(self.items)
        if not items or any(not isinstance(item, BrowserEvidenceItem) for item in items):
            raise BrowserContractError(
                "items must be a non-empty tuple of BrowserEvidenceItem values."
            )
        ids = [item.evidence_id for item in items]
        if len(ids) != len(set(ids)):
            raise BrowserContractError("Duplicate evidence_id values are not allowed.")
        locations = [(item.location_kind.value, item.location) for item in items]
        if len(locations) != len(set(locations)):
            raise BrowserContractError("Duplicate evidence locations are not allowed.")
        object.__setattr__(
            self,
            "items",
            tuple(sorted(items, key=lambda item: item.evidence_id)),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "revision_id": self.revision_id,
            "workspace_id": self.workspace_id,
            "script_artifact_fingerprint": self.script_artifact_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "items": [item.to_dict() for item in self.items],
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint_payload("browser-evidence-manifest-v1", self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}
