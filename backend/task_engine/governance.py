from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import hashlib
import json
import re
from typing import Any, Iterable


GOVERNANCE_SCHEMA_VERSION = "arch-gov-core-001.v1"

_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


class TaskRiskClass(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30
    CRITICAL = 40

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def coerce(cls, value: "TaskRiskClass | str | int") -> "TaskRiskClass":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().upper()
            try:
                return cls[normalized]
            except KeyError as exc:
                raise ValueError(f"Unsupported risk class: {value!r}.") from exc
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                return cls(value)
            except ValueError as exc:
                raise ValueError(f"Unsupported risk class: {value!r}.") from exc
        raise ValueError(f"Unsupported risk class: {value!r}.")


class ExecutionInitiator(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SCHEDULER = "scheduler"
    WORKFLOW = "workflow"
    SYSTEM = "system"


class SideEffectClass(StrEnum):
    NONE = "none"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    CREDENTIAL_USE = "credential_use"
    ARTIFACT_EXPORT = "artifact_export"
    PRODUCTION_CHANGE = "production_change"


class GovernanceRole(StrEnum):
    IMPLEMENTATION = "implementation"
    TEST_EVIDENCE = "test_evidence"
    SECURITY_REVIEW = "security_review"
    INDEPENDENT_REVIEW = "independent_review"


class GovernanceControl(StrEnum):
    IMPLEMENTATION_SELF_CHECK = "implementation_self_check"
    DETERMINISTIC_VALIDATION = "deterministic_validation"
    EVIDENCE_AGENT = "evidence_agent"
    SECURITY_REVIEW = "security_review"
    INDEPENDENT_REVIEW = "independent_review"
    HUMAN_PROMOTION = "human_promotion"


class EvidenceProvenance(StrEnum):
    IMPLEMENTATION_SELF_TEST = "implementation_self_test"
    DETERMINISTIC_VALIDATOR = "deterministic_validator"
    USER_RUN_TEST = "user_run_test"
    INDEPENDENT_TEST = "independent_test"
    EXTERNAL_ON_WIRE_EVIDENCE = "external_on_wire_evidence"
    SECURITY_REVIEW = "security_review"
    HUMAN_APPROVAL = "human_approval"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_token(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 128 or not _TOKEN.fullmatch(normalized):
        raise ValueError(f"Invalid {field_name}: {value!r}.")
    return normalized


def _normalize_tokens(
    values: Iterable[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be an array.")
    return tuple(
        sorted(
            {
                _normalize_token(value, field_name=field_name)
                for value in values
            }
        )
    )


def _normalize_enum_tuple(
    values: Iterable[Any],
    enum_type: type[StrEnum],
    *,
    field_name: str,
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be an array.")
    normalized: set[Any] = set()
    for value in values:
        if isinstance(value, enum_type):
            normalized.add(value)
            continue
        if not isinstance(value, str):
            raise ValueError(f"Unsupported {field_name}: {value!r}.")
        try:
            normalized.add(enum_type(value.strip().lower()))
        except ValueError as exc:
            raise ValueError(
                f"Unsupported {field_name}: {value!r}."
            ) from exc
    return tuple(item for item in enum_type if item in normalized)


@dataclass(frozen=True)
class TaskGovernanceRequest:
    task_id: str
    revision_id: str
    risk_class: TaskRiskClass | str | int
    execution_initiator: ExecutionInitiator | str
    side_effects: tuple[SideEffectClass | str, ...] = field(
        default_factory=tuple
    )
    requested_capabilities: tuple[str, ...] = field(
        default_factory=tuple
    )
    policy_tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        task_id = _normalize_token(self.task_id, field_name="task_id")
        revision_id = _normalize_token(
            self.revision_id,
            field_name="revision_id",
        )
        risk_class = TaskRiskClass.coerce(self.risk_class)
        try:
            initiator = (
                self.execution_initiator
                if isinstance(
                    self.execution_initiator,
                    ExecutionInitiator,
                )
                else ExecutionInitiator(
                    str(self.execution_initiator).strip().lower()
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Unsupported execution_initiator: "
                f"{self.execution_initiator!r}."
            ) from exc
        side_effects = _normalize_enum_tuple(
            self.side_effects,
            SideEffectClass,
            field_name="side_effects",
        )
        if not side_effects:
            side_effects = (SideEffectClass.NONE,)
        elif (
            SideEffectClass.NONE in side_effects
            and len(side_effects) > 1
        ):
            raise ValueError(
                "side_effects cannot combine 'none' with other effects."
            )
        requested_capabilities = _normalize_tokens(
            self.requested_capabilities,
            field_name="requested_capabilities",
        )
        policy_tags = _normalize_tokens(
            self.policy_tags,
            field_name="policy_tags",
        )

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "revision_id", revision_id)
        object.__setattr__(self, "risk_class", risk_class)
        object.__setattr__(
            self,
            "execution_initiator",
            initiator,
        )
        object.__setattr__(self, "side_effects", side_effects)
        object.__setattr__(
            self,
            "requested_capabilities",
            requested_capabilities,
        )
        object.__setattr__(self, "policy_tags", policy_tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "revision_id": self.revision_id,
            "risk_class": self.risk_class.label,
            "execution_initiator": self.execution_initiator.value,
            "side_effects": [
                item.value for item in self.side_effects
            ],
            "requested_capabilities": list(
                self.requested_capabilities
            ),
            "policy_tags": list(self.policy_tags),
        }


@dataclass(frozen=True)
class TaskGovernancePolicy:
    policy_id: str = "minimum-sufficient-governance"
    version: str = "1"
    validator_min_risk: TaskRiskClass = TaskRiskClass.MEDIUM
    evidence_agent_min_risk: TaskRiskClass = TaskRiskClass.HIGH
    independent_review_min_risk: TaskRiskClass = TaskRiskClass.HIGH
    security_review_min_risk: TaskRiskClass = TaskRiskClass.CRITICAL
    human_gate_min_risk: TaskRiskClass = TaskRiskClass.CRITICAL
    evidence_side_effects: tuple[SideEffectClass, ...] = (
        SideEffectClass.CREDENTIAL_USE,
        SideEffectClass.PRODUCTION_CHANGE,
    )
    independent_review_side_effects: tuple[
        SideEffectClass,
        ...,
    ] = (SideEffectClass.PRODUCTION_CHANGE,)
    security_review_side_effects: tuple[SideEffectClass, ...] = (
        SideEffectClass.CREDENTIAL_USE,
        SideEffectClass.PRODUCTION_CHANGE,
    )
    human_gate_side_effects: tuple[SideEffectClass, ...] = (
        SideEffectClass.EXTERNAL_WRITE,
        SideEffectClass.CREDENTIAL_USE,
        SideEffectClass.ARTIFACT_EXPORT,
        SideEffectClass.PRODUCTION_CHANGE,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _normalize_token(
                self.policy_id,
                field_name="policy_id",
            ),
        )
        version = str(self.version).strip()
        if not version or len(version) > 64:
            raise ValueError("Invalid policy version.")
        object.__setattr__(self, "version", version)

        for field_name in (
            "validator_min_risk",
            "evidence_agent_min_risk",
            "independent_review_min_risk",
            "security_review_min_risk",
            "human_gate_min_risk",
        ):
            object.__setattr__(
                self,
                field_name,
                TaskRiskClass.coerce(
                    getattr(self, field_name)
                ),
            )

        for field_name in (
            "evidence_side_effects",
            "independent_review_side_effects",
            "security_review_side_effects",
            "human_gate_side_effects",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_enum_tuple(
                    getattr(self, field_name),
                    SideEffectClass,
                    field_name=field_name,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "validator_min_risk": self.validator_min_risk.label,
            "evidence_agent_min_risk": (
                self.evidence_agent_min_risk.label
            ),
            "independent_review_min_risk": (
                self.independent_review_min_risk.label
            ),
            "security_review_min_risk": (
                self.security_review_min_risk.label
            ),
            "human_gate_min_risk": self.human_gate_min_risk.label,
            "evidence_side_effects": [
                item.value
                for item in self.evidence_side_effects
            ],
            "independent_review_side_effects": [
                item.value
                for item in self.independent_review_side_effects
            ],
            "security_review_side_effects": [
                item.value
                for item in self.security_review_side_effects
            ],
            "human_gate_side_effects": [
                item.value
                for item in self.human_gate_side_effects
            ],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "schema_version": GOVERNANCE_SCHEMA_VERSION,
                    "policy": self.to_dict(),
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class TaskGovernancePlan:
    request: TaskGovernanceRequest
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    controls: tuple[GovernanceControl, ...]
    required_roles: tuple[GovernanceRole, ...]
    required_evidence: tuple[EvidenceProvenance, ...]
    human_gate_required: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GOVERNANCE_SCHEMA_VERSION,
            "request": self.request.to_dict(),
            "policy": {
                "policy_id": self.policy_id,
                "version": self.policy_version,
                "fingerprint": self.policy_fingerprint,
            },
            "controls": [
                item.value for item in self.controls
            ],
            "required_roles": [
                item.value for item in self.required_roles
            ],
            "required_evidence": [
                item.value for item in self.required_evidence
            ],
            "human_gate_required": self.human_gate_required,
            "reason_codes": list(self.reason_codes),
            "requested_capabilities": list(
                self.request.requested_capabilities
            ),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


class MinimumGovernancePlanner:
    """Select the least expensive workflow that satisfies active policy.

    The planner is pure and grants no capabilities. It only derives
    governance requirements from risk, side-effect classes and policy.
    Capability approval, runtime isolation and action authorization
    remain separate boundaries.
    """

    def __init__(
        self,
        policy: TaskGovernancePolicy | None = None,
    ) -> None:
        self.policy = policy or TaskGovernancePolicy()

    def plan(
        self,
        request: TaskGovernanceRequest,
    ) -> TaskGovernancePlan:
        if not isinstance(request, TaskGovernanceRequest):
            raise TypeError(
                "request must be TaskGovernanceRequest."
            )

        controls: set[GovernanceControl] = {
            GovernanceControl.IMPLEMENTATION_SELF_CHECK,
        }
        roles: set[GovernanceRole] = {
            GovernanceRole.IMPLEMENTATION
        }
        evidence: set[EvidenceProvenance] = {
            EvidenceProvenance.IMPLEMENTATION_SELF_TEST,
        }
        reasons: set[str] = {
            "BASE_IMPLEMENTATION_SELF_CHECK"
        }
        effects = set(request.side_effects)
        risk = request.risk_class

        if risk >= self.policy.validator_min_risk:
            controls.add(
                GovernanceControl.DETERMINISTIC_VALIDATION
            )
            evidence.add(
                EvidenceProvenance.DETERMINISTIC_VALIDATOR
            )
            reasons.add(
                "RISK_REQUIRES_DETERMINISTIC_VALIDATION"
            )

        if (
            risk >= self.policy.evidence_agent_min_risk
            or effects.intersection(
                self.policy.evidence_side_effects
            )
        ):
            controls.add(GovernanceControl.EVIDENCE_AGENT)
            roles.add(GovernanceRole.TEST_EVIDENCE)
            reasons.add("EVIDENCE_AGENT_REQUIRED")

        if (
            risk >= self.policy.security_review_min_risk
            or effects.intersection(
                self.policy.security_review_side_effects
            )
        ):
            controls.add(GovernanceControl.SECURITY_REVIEW)
            roles.add(GovernanceRole.SECURITY_REVIEW)
            evidence.add(EvidenceProvenance.SECURITY_REVIEW)
            reasons.add("SECURITY_REVIEW_REQUIRED")

        if (
            risk >= self.policy.independent_review_min_risk
            or effects.intersection(
                self.policy.independent_review_side_effects
            )
        ):
            controls.add(
                GovernanceControl.INDEPENDENT_REVIEW
            )
            roles.add(GovernanceRole.INDEPENDENT_REVIEW)
            evidence.add(EvidenceProvenance.INDEPENDENT_TEST)
            reasons.add("INDEPENDENT_REVIEW_REQUIRED")

        human_gate_required = (
            risk >= self.policy.human_gate_min_risk
            or bool(
                effects.intersection(
                    self.policy.human_gate_side_effects
                )
            )
        )
        if human_gate_required:
            controls.add(GovernanceControl.HUMAN_PROMOTION)
            evidence.add(EvidenceProvenance.HUMAN_APPROVAL)
            reasons.add("HUMAN_PROMOTION_REQUIRED")

        return TaskGovernancePlan(
            request=request,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            policy_fingerprint=self.policy.fingerprint,
            controls=tuple(
                item
                for item in GovernanceControl
                if item in controls
            ),
            required_roles=tuple(
                item
                for item in GovernanceRole
                if item in roles
            ),
            required_evidence=tuple(
                item
                for item in EvidenceProvenance
                if item in evidence
            ),
            human_gate_required=human_gate_required,
            reason_codes=tuple(sorted(reasons)),
        )
