from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from backend.runtime_policy import DataClassification


AGENT_POLICY_PROFILE_SCHEMA_VERSION = "p3-002.1a.agent-profile"
AGENT_POLICY_DECISION_SCHEMA_VERSION = "p3-002.1a.agent-decision"

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_VERSION_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+-]*$")
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Z0-9_:-]+$")


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
            "Agent policy values must be JSON-serializable."
        ) from exc


def _normalize_identifier(
    value: str,
    *,
    field_name: str,
    max_length: int = 128,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > max_length:
        raise ValueError(
            f"{field_name} exceeds the {max_length}-character limit."
        )
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} contains unsupported characters."
        )
    return normalized


def _normalize_version(
    value: str,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > 64:
        raise ValueError(
            f"{field_name} exceeds the 64-character limit."
        )
    if not _VERSION_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} contains unsupported characters."
        )
    return normalized


def _normalize_model(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > 255:
        raise ValueError(
            f"{field_name} exceeds the 255-character limit."
        )
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(
            f"{field_name} contains unsupported control characters."
        )
    return normalized


def _collection_values(
    values: Iterable[Any],
    *,
    field_name: str,
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError(f"{field_name} must be an array.")
    try:
        return tuple(values)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an array.") from exc


def _normalize_identifiers(
    values: Iterable[str],
    *,
    field_name: str,
    required: bool = False,
    max_items: int = 128,
) -> tuple[str, ...]:
    raw_values = _collection_values(values, field_name=field_name)
    normalized = {
        _normalize_identifier(value, field_name=field_name)
        for value in raw_values
    }
    if required and not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > max_items:
        raise ValueError(
            f"{field_name} supports at most {max_items} entries."
        )
    return tuple(sorted(normalized))


def _as_enum(value: Any, enum_type: type[StrEnum], field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ValueError(f"Unsupported {field_name}: {value!r}.")
    try:
        return enum_type(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unsupported {field_name}: {value!r}.") from exc


def _normalize_capabilities(
    values: Iterable[AgentCapability | str],
    *,
    field_name: str,
) -> tuple[AgentCapability, ...]:
    raw_values = _collection_values(values, field_name=field_name)
    normalized = {
        _as_enum(value, AgentCapability, field_name)
        for value in raw_values
    }
    return tuple(
        capability
        for capability in AgentCapability
        if capability in normalized
    )


def _normalize_tool_capabilities(
    value: Mapping[str, Iterable[AgentCapability | str]],
    *,
    allowed_tools: tuple[str, ...],
) -> Mapping[str, tuple[AgentCapability, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError("tool_capabilities must be an object.")
    normalized: dict[str, tuple[AgentCapability, ...]] = {}
    for raw_tool_id, raw_capabilities in value.items():
        tool_id = _normalize_identifier(
            raw_tool_id,
            field_name="tool_capabilities tool id",
        )
        if tool_id in normalized:
            raise ValueError(
                f"Duplicate tool_capabilities binding: {tool_id}."
            )
        normalized[tool_id] = _normalize_capabilities(
            raw_capabilities,
            field_name=f"tool_capabilities[{tool_id}]",
        )

    allowed_set = set(allowed_tools)
    bound_set = set(normalized)
    missing = sorted(allowed_set - bound_set)
    unknown = sorted(bound_set - allowed_set)
    if missing:
        raise ValueError(
            "tool_capabilities is missing allowed tools: "
            + ", ".join(missing)
        )
    if unknown:
        raise ValueError(
            "tool_capabilities contains unknown tools: "
            + ", ".join(unknown)
        )
    return MappingProxyType(
        {
            tool_id: normalized[tool_id]
            for tool_id in sorted(normalized)
        }
    )


def _normalize_classifications(
    values: Iterable[DataClassification | str],
) -> tuple[DataClassification, ...]:
    raw_values = _collection_values(
        values,
        field_name="allowed_classifications",
    )
    normalized: set[DataClassification] = set()
    for value in raw_values:
        if isinstance(value, DataClassification):
            normalized.add(value)
            continue
        if not isinstance(value, str):
            raise ValueError(
                "allowed_classifications contains unsupported values."
            )
        try:
            normalized.add(DataClassification(value.strip()))
        except ValueError as exc:
            raise ValueError(
                "allowed_classifications contains unsupported values."
            ) from exc
    if not normalized:
        raise ValueError("allowed_classifications must not be empty.")
    return tuple(
        classification
        for classification in DataClassification
        if classification in normalized
    )


def _normalize_domain(
    value: str,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must contain strings.")
    normalized = value.strip().lower().rstrip(".")
    if normalized == "*":
        raise ValueError(
            f"{field_name} wildcard is not permitted; list exact hosts."
        )
    if not _DOMAIN_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must contain exact DNS hostnames."
        )
    return normalized


class AgentNetworkAccess(StrEnum):
    DENIED = "denied"
    RESTRICTED = "restricted"
    ALLOWED = "allowed"


class AgentFilesystemAccess(StrEnum):
    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class AgentCapability(StrEnum):
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    REPOSITORY_STAGE = "repository_stage"
    REPOSITORY_COMMIT = "repository_commit"
    REMOTE_MUTATION = "remote_mutation"
    EXTERNAL_NETWORK = "external_network"
    CODE_EXECUTION = "code_execution"
    PRODUCTION_CHANGE = "production_change"


class AgentPolicyAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class AgentPolicyProfile:
    """Auditable restrictions layered above canonical platform policy.

    A profile can deny, require approval, or leave an operation eligible for
    canonical Runtime Policy, Workspace Policy and Human Control evaluation.
    It never grants permission on its own.
    """

    profile_id: str
    version: str
    audit_version: str
    allowed_tools: tuple[str, ...]
    tool_capabilities: Mapping[str, tuple[AgentCapability, ...]]
    denied_actions: tuple[str, ...]
    mandatory_checks: tuple[str, ...]
    approval_conditions: tuple[AgentCapability, ...]
    external_domains: tuple[str, ...]
    allowed_classifications: tuple[DataClassification, ...]
    primary_model: str
    reviewer_model: str
    network_access: AgentNetworkAccess = AgentNetworkAccess.DENIED
    filesystem_access: AgentFilesystemAccess = AgentFilesystemAccess.READ_ONLY

    def __post_init__(self) -> None:
        profile_id = _normalize_identifier(
            self.profile_id,
            field_name="profile_id",
            max_length=64,
        )
        version = _normalize_version(self.version, field_name="version")
        audit_version = _normalize_version(
            self.audit_version,
            field_name="audit_version",
        )
        allowed_tools = _normalize_identifiers(
            self.allowed_tools,
            field_name="allowed_tools",
            required=True,
        )
        tool_capabilities = _normalize_tool_capabilities(
            self.tool_capabilities,
            allowed_tools=allowed_tools,
        )
        denied_actions = _normalize_identifiers(
            self.denied_actions,
            field_name="denied_actions",
        )
        mandatory_checks = _normalize_identifiers(
            self.mandatory_checks,
            field_name="mandatory_checks",
            required=True,
        )
        approval_conditions = _normalize_capabilities(
            self.approval_conditions,
            field_name="approval_conditions",
        )
        allowed_classifications = _normalize_classifications(
            self.allowed_classifications
        )
        primary_model = _normalize_model(
            self.primary_model,
            field_name="primary_model",
        )
        reviewer_model = _normalize_model(
            self.reviewer_model,
            field_name="reviewer_model",
        )
        if primary_model == reviewer_model:
            raise ValueError(
                "reviewer_model must differ from primary_model."
            )
        network_access = _as_enum(
            self.network_access,
            AgentNetworkAccess,
            "network_access",
        )
        filesystem_access = _as_enum(
            self.filesystem_access,
            AgentFilesystemAccess,
            "filesystem_access",
        )
        external_domains = tuple(
            sorted(
                {
                    _normalize_domain(
                        domain,
                        field_name="external_domains",
                    )
                    for domain in _collection_values(
                        self.external_domains,
                        field_name="external_domains",
                    )
                }
            )
        )

        if (
            network_access == AgentNetworkAccess.DENIED
            and external_domains
        ):
            raise ValueError(
                "external_domains must be empty when network_access is denied."
            )
        if (
            network_access == AgentNetworkAccess.ALLOWED
            and not external_domains
        ):
            raise ValueError(
                "external_domains must explicitly list exact hosts when "
                "network_access is allowed."
            )

        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "audit_version", audit_version)
        object.__setattr__(self, "allowed_tools", allowed_tools)
        object.__setattr__(
            self,
            "tool_capabilities",
            tool_capabilities,
        )
        object.__setattr__(self, "denied_actions", denied_actions)
        object.__setattr__(self, "mandatory_checks", mandatory_checks)
        object.__setattr__(
            self,
            "approval_conditions",
            approval_conditions,
        )
        object.__setattr__(self, "external_domains", external_domains)
        object.__setattr__(
            self,
            "allowed_classifications",
            allowed_classifications,
        )
        object.__setattr__(self, "primary_model", primary_model)
        object.__setattr__(self, "reviewer_model", reviewer_model)
        object.__setattr__(self, "network_access", network_access)
        object.__setattr__(self, "filesystem_access", filesystem_access)

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_json(
            {
                "schema_version": AGENT_POLICY_PROFILE_SCHEMA_VERSION,
                **self.to_dict(),
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "audit_version": self.audit_version,
            "allowed_tools": list(self.allowed_tools),
            "tool_capabilities": {
                tool_id: [
                    capability.value
                    for capability in capabilities
                ]
                for tool_id, capabilities in self.tool_capabilities.items()
            },
            "denied_actions": list(self.denied_actions),
            "mandatory_checks": list(self.mandatory_checks),
            "approval_conditions": [
                condition.value
                for condition in self.approval_conditions
            ],
            "external_domains": list(self.external_domains),
            "allowed_classifications": [
                classification.value
                for classification in self.allowed_classifications
            ],
            "primary_model": self.primary_model,
            "reviewer_model": self.reviewer_model,
            "network_access": self.network_access.value,
            "filesystem_access": self.filesystem_access.value,
        }


@dataclass(frozen=True)
class AgentToolRequest:
    tool_id: str
    action_id: str
    data_classification: DataClassification
    capabilities: tuple[AgentCapability, ...] = field(default_factory=tuple)
    external_domain: str | None = None
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        tool_id = _normalize_identifier(
            self.tool_id,
            field_name="tool_id",
        )
        action_id = _normalize_identifier(
            self.action_id,
            field_name="action_id",
        )
        try:
            if isinstance(
                self.data_classification,
                DataClassification,
            ):
                data_classification = self.data_classification
            elif isinstance(self.data_classification, str):
                data_classification = DataClassification(
                    self.data_classification.strip()
                )
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "Unsupported data_classification: "
                f"{self.data_classification!r}."
            ) from exc
        capabilities = _normalize_capabilities(
            self.capabilities,
            field_name="capabilities",
        )
        external_domain = (
            None
            if self.external_domain is None
            else _normalize_domain(
                self.external_domain,
                field_name="external_domain",
            )
        )
        workspace_id = (
            None
            if self.workspace_id is None
            else _normalize_identifier(
                self.workspace_id,
                field_name="workspace_id",
                max_length=64,
            )
        )
        uses_external_network = (
            AgentCapability.EXTERNAL_NETWORK in capabilities
        )
        if uses_external_network and external_domain is None:
            raise ValueError(
                "external_domain is required for external network capability."
            )
        if external_domain is not None and not uses_external_network:
            capabilities = _normalize_capabilities(
                (*capabilities, AgentCapability.EXTERNAL_NETWORK),
                field_name="capabilities",
            )

        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(
            self,
            "data_classification",
            data_classification,
        )
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "external_domain", external_domain)
        object.__setattr__(self, "workspace_id", workspace_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "action_id": self.action_id,
            "data_classification": self.data_classification.value,
            "capabilities": [
                capability.value
                for capability in self.capabilities
            ],
            "external_domain": self.external_domain,
            "workspace_id": self.workspace_id,
        }


@dataclass(frozen=True)
class AgentPolicyDecision:
    profile_id: str
    profile_fingerprint: str
    action: AgentPolicyAction
    reason_codes: tuple[str, ...]
    mandatory_checks: tuple[str, ...]
    triggered_approval_conditions: tuple[AgentCapability, ...]
    effective_capabilities: tuple[AgentCapability, ...]
    request: AgentToolRequest

    def __post_init__(self) -> None:
        profile_id = _normalize_identifier(
            self.profile_id,
            field_name="profile_id",
            max_length=64,
        )
        if (
            not isinstance(self.profile_fingerprint, str)
            or not _SHA256_PATTERN.fullmatch(self.profile_fingerprint)
        ):
            raise ValueError(
                "profile_fingerprint must be a 64-character SHA-256 digest."
            )
        action = _as_enum(self.action, AgentPolicyAction, "action")
        raw_reason_codes = _collection_values(
            self.reason_codes,
            field_name="reason_codes",
        )
        reason_codes: list[str] = []
        for raw_code in raw_reason_codes:
            if not isinstance(raw_code, str):
                raise ValueError("reason_codes must contain strings.")
            code = raw_code.strip().upper()
            if not code or not _REASON_CODE_PATTERN.fullmatch(code):
                raise ValueError(
                    f"Unsupported agent policy reason code: {raw_code!r}."
                )
            if code not in reason_codes:
                reason_codes.append(code)
        if not reason_codes:
            raise ValueError("reason_codes must not be empty.")
        mandatory_checks = _normalize_identifiers(
            self.mandatory_checks,
            field_name="mandatory_checks",
            required=True,
        )
        triggered = _normalize_capabilities(
            self.triggered_approval_conditions,
            field_name="triggered_approval_conditions",
        )
        effective_capabilities = _normalize_capabilities(
            self.effective_capabilities,
            field_name="effective_capabilities",
        )
        if not isinstance(self.request, AgentToolRequest):
            raise ValueError("request must be an AgentToolRequest.")
        if not set(self.request.capabilities).issubset(
            effective_capabilities
        ):
            raise ValueError(
                "effective_capabilities must include request capabilities."
            )
        if not set(triggered).issubset(effective_capabilities):
            raise ValueError(
                "Triggered approval conditions must be effective capabilities."
            )
        if action == AgentPolicyAction.REQUIRE_APPROVAL and not triggered:
            raise ValueError(
                "require_approval decision needs a triggered condition."
            )
        if action == AgentPolicyAction.ALLOW:
            if triggered:
                raise ValueError(
                    "allow decision cannot have triggered approval conditions."
                )
            if tuple(reason_codes) != (
                "PROFILE_CONSTRAINTS_SATISFIED",
            ):
                raise ValueError(
                    "allow decision requires the satisfied reason code."
                )
        if action == AgentPolicyAction.REQUIRE_APPROVAL:
            expected_codes = tuple(
                "PROFILE_APPROVAL_" + condition.value.upper()
                for condition in triggered
            )
            if tuple(reason_codes) != expected_codes:
                raise ValueError(
                    "require_approval reason codes must match its conditions."
                )
        if action == AgentPolicyAction.DENY and (
            "PROFILE_CONSTRAINTS_SATISFIED" in reason_codes
            or any(
                code.startswith("PROFILE_APPROVAL_")
                for code in reason_codes
            )
        ):
            raise ValueError("deny decision contains incompatible reasons.")

        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(
            self,
            "profile_fingerprint",
            self.profile_fingerprint.lower(),
        )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason_codes", tuple(reason_codes))
        object.__setattr__(self, "mandatory_checks", mandatory_checks)
        object.__setattr__(
            self,
            "triggered_approval_conditions",
            triggered,
        )
        object.__setattr__(
            self,
            "effective_capabilities",
            effective_capabilities,
        )

    @property
    def canonical_policy_required(self) -> bool:
        return True

    @property
    def allowed_by_profile(self) -> bool:
        return self.action == AgentPolicyAction.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "action": self.action.value,
            "reason_codes": list(self.reason_codes),
            "mandatory_checks": list(self.mandatory_checks),
            "triggered_approval_conditions": [
                condition.value
                for condition in self.triggered_approval_conditions
            ],
            "effective_capabilities": [
                capability.value
                for capability in self.effective_capabilities
            ],
            "request": self.request.to_dict(),
            "canonical_policy_required": self.canonical_policy_required,
        }

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_json(
            {
                "schema_version": AGENT_POLICY_DECISION_SCHEMA_VERSION,
                **self.to_dict(),
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AgentPolicyEvaluator:
    """Evaluate profile restrictions without replacing canonical policy."""

    def evaluate(
        self,
        profile: AgentPolicyProfile,
        request: AgentToolRequest,
    ) -> AgentPolicyDecision:
        deny_reasons: list[str] = []

        if request.tool_id not in profile.allowed_tools:
            deny_reasons.append("PROFILE_TOOL_NOT_ALLOWED")
        if request.action_id in profile.denied_actions:
            deny_reasons.append("PROFILE_ACTION_DENIED")
        if (
            request.data_classification
            not in profile.allowed_classifications
        ):
            deny_reasons.append("PROFILE_CLASSIFICATION_DENIED")

        required_capabilities = profile.tool_capabilities.get(
            request.tool_id,
            (),
        )
        effective_capabilities = _normalize_capabilities(
            (*required_capabilities, *request.capabilities),
            field_name="effective_capabilities",
        )
        capabilities = set(effective_capabilities)
        filesystem_read = AgentCapability.FILESYSTEM_READ in capabilities
        filesystem_write = AgentCapability.FILESYSTEM_WRITE in capabilities
        if (
            profile.filesystem_access == AgentFilesystemAccess.NONE
            and (filesystem_read or filesystem_write)
        ):
            deny_reasons.append("PROFILE_FILESYSTEM_DENIED")
        elif (
            profile.filesystem_access == AgentFilesystemAccess.READ_ONLY
            and filesystem_write
        ):
            deny_reasons.append("PROFILE_FILESYSTEM_WRITE_DENIED")

        if AgentCapability.EXTERNAL_NETWORK in capabilities:
            if profile.network_access == AgentNetworkAccess.DENIED:
                deny_reasons.append("PROFILE_NETWORK_DENIED")
            elif request.external_domain is None:
                deny_reasons.append("PROFILE_EXTERNAL_DOMAIN_REQUIRED")
            elif request.external_domain not in profile.external_domains:
                deny_reasons.append("PROFILE_DOMAIN_NOT_ALLOWED")

        triggered = tuple(
            condition
            for condition in profile.approval_conditions
            if condition in capabilities
        )

        if deny_reasons:
            action = AgentPolicyAction.DENY
            reason_codes = tuple(deny_reasons)
        elif triggered:
            action = AgentPolicyAction.REQUIRE_APPROVAL
            reason_codes = tuple(
                "PROFILE_APPROVAL_" + condition.value.upper()
                for condition in triggered
            )
        else:
            action = AgentPolicyAction.ALLOW
            reason_codes = ("PROFILE_CONSTRAINTS_SATISFIED",)

        return AgentPolicyDecision(
            profile_id=profile.profile_id,
            profile_fingerprint=profile.fingerprint,
            action=action,
            reason_codes=reason_codes,
            mandatory_checks=profile.mandatory_checks,
            triggered_approval_conditions=triggered,
            effective_capabilities=effective_capabilities,
            request=request,
        )


class AgentProfileNotFoundError(LookupError):
    pass


class AgentProfileRegistry:
    def __init__(self, profiles: Iterable[AgentPolicyProfile]) -> None:
        self._profiles: dict[str, AgentPolicyProfile] = {}
        for profile in profiles:
            if profile.profile_id in self._profiles:
                raise ValueError(
                    f"Duplicate agent profile: {profile.profile_id}."
                )
            self._profiles[profile.profile_id] = profile

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def list_profiles(self) -> tuple[AgentPolicyProfile, ...]:
        return tuple(
            self._profiles[profile_id]
            for profile_id in self.list_ids()
        )

    def get(self, profile_id: str) -> AgentPolicyProfile:
        normalized = _normalize_identifier(
            profile_id,
            field_name="profile_id",
            max_length=64,
        )
        profile = self._profiles.get(normalized)
        if profile is None:
            raise AgentProfileNotFoundError(
                f"Unknown agent profile: {normalized}."
            )
        return profile

    def evaluate(
        self,
        profile_id: str,
        request: AgentToolRequest,
    ) -> AgentPolicyDecision:
        return AgentPolicyEvaluator().evaluate(
            self.get(profile_id),
            request,
        )
