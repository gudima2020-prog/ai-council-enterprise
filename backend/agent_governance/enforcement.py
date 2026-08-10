from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Iterable

from backend.agent_governance.core import (
    AgentPolicyAction,
    AgentPolicyDecision,
    AgentPolicyEvaluator,
    AgentPolicyProfile,
    AgentToolRequest,
)
from backend.runtime_policy import PolicyAction, RuntimePolicyDecision


AGENT_BEFORE_TOOL_ENFORCEMENT_SCHEMA_VERSION = (
    "p3-002.2a.before-tool-enforcement"
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REASON = re.compile(r"^[A-Z0-9_:-]+$")
_WORKSPACE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _workspace(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("workspace_id must be a string.")
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > 64
        or not _WORKSPACE.fullmatch(normalized)
    ):
        raise ValueError("Invalid workspace_id.")
    return normalized


def _fingerprint(value: str, field: str, *, lower: bool = True) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a SHA-256 digest.")
    return value.lower() if lower else value


def _reason_codes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("reason_codes must be an array.")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("reason_codes must contain strings.")
        code = raw.strip().upper()
        if not code or not _REASON.fullmatch(code):
            raise ValueError(f"Invalid reason code: {raw!r}.")
        if code not in result:
            result.append(code)
    if not result:
        raise ValueError("reason_codes must not be empty.")
    return tuple(result)


class AgentEnforcementLayer(StrEnum):
    TOOL_REGISTRY = "tool_registry"
    WORKSPACE_POLICY = "workspace_policy"
    RUNTIME_POLICY = "runtime_policy"
    HUMAN_CONTROL = "human_control"


class AgentEnforcementAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_ISOLATION = "require_isolation"
    DENY = "deny"


_ALLOWED_ACTIONS = {
    AgentEnforcementLayer.TOOL_REGISTRY: {
        AgentEnforcementAction.ALLOW,
        AgentEnforcementAction.DENY,
    },
    AgentEnforcementLayer.WORKSPACE_POLICY: {
        AgentEnforcementAction.ALLOW,
        AgentEnforcementAction.REQUIRE_APPROVAL,
        AgentEnforcementAction.DENY,
    },
    AgentEnforcementLayer.RUNTIME_POLICY: set(AgentEnforcementAction),
    AgentEnforcementLayer.HUMAN_CONTROL: {
        AgentEnforcementAction.ALLOW,
        AgentEnforcementAction.REQUIRE_APPROVAL,
        AgentEnforcementAction.DENY,
    },
}


@dataclass(frozen=True)
class AgentEnforcementLayerDecision:
    layer: AgentEnforcementLayer
    workspace_id: str
    action: AgentEnforcementAction
    reason_codes: tuple[str, ...]
    policy_version: str
    upstream_fingerprint: str

    def __post_init__(self) -> None:
        layer = (
            self.layer
            if isinstance(self.layer, AgentEnforcementLayer)
            else AgentEnforcementLayer(self.layer)
        )
        action = (
            self.action
            if isinstance(self.action, AgentEnforcementAction)
            else AgentEnforcementAction(self.action)
        )
        if action not in _ALLOWED_ACTIONS[layer]:
            raise ValueError(
                f"{layer.value} does not support action {action.value}."
            )
        version = str(self.policy_version).strip()
        if not version or len(version) > 128:
            raise ValueError("Invalid policy_version.")
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "workspace_id", _workspace(self.workspace_id))
        object.__setattr__(
            self, "reason_codes", _reason_codes(self.reason_codes)
        )
        object.__setattr__(self, "policy_version", version)
        object.__setattr__(
            self,
            "upstream_fingerprint",
            _fingerprint(
                self.upstream_fingerprint,
                "upstream_fingerprint",
            ),
        )

    @classmethod
    def from_runtime_policy(
        cls,
        *,
        workspace_id: str,
        decision: RuntimePolicyDecision,
    ) -> "AgentEnforcementLayerDecision":
        if not isinstance(decision, RuntimePolicyDecision):
            raise ValueError("decision must be a RuntimePolicyDecision.")
        mapping = {
            PolicyAction.ALLOW: AgentEnforcementAction.ALLOW,
            PolicyAction.REQUIRE_APPROVAL: (
                AgentEnforcementAction.REQUIRE_APPROVAL
            ),
            PolicyAction.REQUIRE_ISOLATION: (
                AgentEnforcementAction.REQUIRE_ISOLATION
            ),
            PolicyAction.DENY: AgentEnforcementAction.DENY,
        }
        return cls(
            layer=AgentEnforcementLayer.RUNTIME_POLICY,
            workspace_id=workspace_id,
            action=mapping[decision.action],
            reason_codes=decision.reason_codes,
            policy_version=decision.policy_version,
            upstream_fingerprint=decision.fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "workspace_id": self.workspace_id,
            "action": self.action.value,
            "reason_codes": list(self.reason_codes),
            "policy_version": self.policy_version,
            "upstream_fingerprint": self.upstream_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class AgentBeforeToolExecutionDecision:
    workspace_id: str | None
    expected_profile_fingerprint: str
    profile_decision: AgentPolicyDecision
    layer_decisions: tuple[AgentEnforcementLayerDecision, ...]
    action: AgentEnforcementAction
    reason_codes: tuple[str, ...]
    approval_required: bool = False
    isolation_required: bool = False

    def __post_init__(self) -> None:
        action = (
            self.action
            if isinstance(self.action, AgentEnforcementAction)
            else AgentEnforcementAction(self.action)
        )
        layers = tuple(self.layer_decisions)
        if not all(
            isinstance(item, AgentEnforcementLayerDecision)
            for item in layers
        ):
            raise ValueError("Invalid layer_decisions.")
        if not isinstance(self.profile_decision, AgentPolicyDecision):
            raise ValueError("Invalid profile_decision.")
        if action == AgentEnforcementAction.ALLOW and (
            self.approval_required or self.isolation_required
        ):
            raise ValueError("allow cannot have unresolved controls.")
        if (
            action == AgentEnforcementAction.REQUIRE_APPROVAL
            and not self.approval_required
        ):
            raise ValueError("require_approval needs approval_required.")
        if (
            action == AgentEnforcementAction.REQUIRE_ISOLATION
            and not self.isolation_required
        ):
            raise ValueError("require_isolation needs isolation_required.")
        if action == AgentEnforcementAction.DENY and (
            self.approval_required or self.isolation_required
        ):
            raise ValueError("deny cannot expose executable controls.")
        object.__setattr__(
            self,
            "workspace_id",
            None if self.workspace_id is None else _workspace(self.workspace_id),
        )
        object.__setattr__(
            self,
            "expected_profile_fingerprint",
            _fingerprint(
                self.expected_profile_fingerprint,
                "expected_profile_fingerprint",
                lower=False,
            ),
        )
        object.__setattr__(self, "layer_decisions", layers)
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self, "reason_codes", _reason_codes(self.reason_codes)
        )

    @property
    def executable(self) -> bool:
        return self.action == AgentEnforcementAction.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "expected_profile_fingerprint": self.expected_profile_fingerprint,
            "profile_decision": self.profile_decision.to_dict(),
            "profile_decision_fingerprint": self.profile_decision.fingerprint,
            "layer_decisions": [
                {
                    **item.to_dict(),
                    "decision_fingerprint": item.fingerprint,
                }
                for item in self.layer_decisions
            ],
            "action": self.action.value,
            "reason_codes": list(self.reason_codes),
            "approval_required": self.approval_required,
            "isolation_required": self.isolation_required,
            "mandatory_checks": list(self.profile_decision.mandatory_checks),
            "executable": self.executable,
            "canonical_policy_required": True,
        }

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": AGENT_BEFORE_TOOL_ENFORCEMENT_SCHEMA_VERSION,
            **self.to_dict(),
        }
        return hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()


class BeforeToolExecutionEnforcer:
    """Pure fail-closed join before a real tool handler can execute.

    AgentToolRequest must be constructed by a trusted adapter from registry
    metadata. Model-generated arguments are not authoritative metadata.
    """

    REQUIRED_LAYERS = (
        AgentEnforcementLayer.TOOL_REGISTRY,
        AgentEnforcementLayer.WORKSPACE_POLICY,
        AgentEnforcementLayer.RUNTIME_POLICY,
    )

    def evaluate(
        self,
        *,
        profile: AgentPolicyProfile,
        expected_profile_fingerprint: str,
        request: AgentToolRequest,
        layer_decisions: Iterable[AgentEnforcementLayerDecision],
    ) -> AgentBeforeToolExecutionDecision:
        if not isinstance(profile, AgentPolicyProfile):
            raise ValueError("profile must be AgentPolicyProfile.")
        if not isinstance(request, AgentToolRequest):
            raise ValueError("request must be AgentToolRequest.")
        expected = _fingerprint(
            expected_profile_fingerprint,
            "expected_profile_fingerprint",
            lower=False,
        )
        profile_decision = AgentPolicyEvaluator().evaluate(profile, request)
        layers = tuple(layer_decisions)
        if not all(
            isinstance(item, AgentEnforcementLayerDecision)
            for item in layers
        ):
            raise ValueError("Invalid layer_decisions.")

        reasons: list[str] = []
        workspace_id = request.workspace_id
        if workspace_id is None:
            reasons.append("ENFORCEMENT_WORKSPACE_REQUIRED")
        if expected != profile.fingerprint:
            reasons.append(
                "ENFORCEMENT_PROFILE_FINGERPRINT_MISMATCH"
            )

        by_layer: dict[
            AgentEnforcementLayer, AgentEnforcementLayerDecision
        ] = {}
        duplicates: set[AgentEnforcementLayer] = set()
        for item in layers:
            if item.layer in by_layer:
                duplicates.add(item.layer)
            else:
                by_layer[item.layer] = item
        for layer in sorted(duplicates, key=lambda value: value.value):
            reasons.append(
                "ENFORCEMENT_DUPLICATE_"
                + layer.value.upper()
                + "_DECISION"
            )
        for layer in self.REQUIRED_LAYERS:
            if layer not in by_layer:
                reasons.append(
                    "ENFORCEMENT_MISSING_"
                    + layer.value.upper()
                    + "_DECISION"
                )
        if workspace_id is not None:
            for item in layers:
                if item.workspace_id != workspace_id:
                    reasons.append(
                        "ENFORCEMENT_"
                        + item.layer.value.upper()
                        + "_WORKSPACE_MISMATCH"
                    )

        if profile_decision.action == AgentPolicyAction.DENY:
            reasons.extend(profile_decision.reason_codes)
        for item in layers:
            if item.action == AgentEnforcementAction.DENY:
                reasons.extend(self._qualified(item))

        if reasons:
            return self._make(
                workspace_id,
                expected,
                profile_decision,
                layers,
                AgentEnforcementAction.DENY,
                reasons,
            )

        isolation_reasons: list[str] = []
        for item in layers:
            if item.action == AgentEnforcementAction.REQUIRE_ISOLATION:
                isolation_reasons.extend(self._qualified(item))

        approval_reasons: list[str] = []
        approval_requested = (
            profile_decision.action == AgentPolicyAction.REQUIRE_APPROVAL
        )
        if approval_requested:
            approval_reasons.extend(profile_decision.reason_codes)
        for item in layers:
            if (
                item.layer != AgentEnforcementLayer.HUMAN_CONTROL
                and item.action
                == AgentEnforcementAction.REQUIRE_APPROVAL
            ):
                approval_requested = True
                approval_reasons.extend(self._qualified(item))

        human = by_layer.get(AgentEnforcementLayer.HUMAN_CONTROL)
        approval_required = approval_requested and not (
            human is not None
            and human.action == AgentEnforcementAction.ALLOW
        )
        if (
            human is not None
            and human.action == AgentEnforcementAction.REQUIRE_APPROVAL
        ):
            approval_required = True
            approval_reasons.extend(self._qualified(human))

        isolation_required = bool(isolation_reasons)
        control_reasons: list[str] = []
        if isolation_required:
            control_reasons.extend(isolation_reasons)
            control_reasons.append("ENFORCEMENT_ISOLATION_REQUIRED")
        if approval_required:
            control_reasons.extend(approval_reasons)
            control_reasons.append("ENFORCEMENT_APPROVAL_REQUIRED")
        elif approval_requested:
            control_reasons.append("ENFORCEMENT_APPROVAL_SATISFIED")

        if isolation_required:
            action = AgentEnforcementAction.REQUIRE_ISOLATION
        elif approval_required:
            action = AgentEnforcementAction.REQUIRE_APPROVAL
        else:
            action = AgentEnforcementAction.ALLOW
            if not control_reasons:
                control_reasons = ["BEFORE_TOOL_EXECUTION_ALLOWED"]

        return self._make(
            workspace_id,
            expected,
            profile_decision,
            layers,
            action,
            control_reasons,
            approval_required=approval_required,
            isolation_required=isolation_required,
        )

    @staticmethod
    def _qualified(
        decision: AgentEnforcementLayerDecision,
    ) -> tuple[str, ...]:
        prefix = decision.layer.value.upper() + "_"
        return tuple(prefix + code for code in decision.reason_codes)

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        for value in values:
            if value not in result:
                result.append(value)
        return tuple(result)

    @classmethod
    def _make(
        cls,
        workspace_id: str | None,
        expected: str,
        profile_decision: AgentPolicyDecision,
        layers: tuple[AgentEnforcementLayerDecision, ...],
        action: AgentEnforcementAction,
        reasons: Iterable[str],
        *,
        approval_required: bool = False,
        isolation_required: bool = False,
    ) -> AgentBeforeToolExecutionDecision:
        return AgentBeforeToolExecutionDecision(
            workspace_id=workspace_id,
            expected_profile_fingerprint=expected,
            profile_decision=profile_decision,
            layer_decisions=layers,
            action=action,
            reason_codes=cls._unique(reasons),
            approval_required=approval_required,
            isolation_required=isolation_required,
        )
