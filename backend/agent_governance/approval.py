from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any

from backend.agent_governance.enforcement import (
    AgentBeforeToolExecutionDecision,
    AgentEnforcementAction,
    AgentEnforcementLayer,
    AgentEnforcementLayerDecision,
)
from backend.agent_governance.enforcement_adapters import (
    TrustedAgentToolBinding,
)
from backend.policy_approvals import (
    PolicyApprovalCore,
    PolicyApprovalRecord,
    PolicyApprovalScope,
    PolicyApprovalScopeError,
    PolicyApprovalStatus,
)


AGENT_HUMAN_APPROVAL_SCHEMA_VERSION = (
    "p3-002.2b-b.agent-human-approval"
)
AGENT_HUMAN_APPROVAL_SUBJECT_TYPE = "agent_tool_execution"

_HUMAN_CONTROL_POLICY_VERSION = (
    "p3-002.2b-b.human-control-adapter"
)
_AGENT_TOOL_INPUT_HASH_DOMAIN = (
    b"ai-council-agent-tool-input-v1\\x00"
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        _canonical(value).encode("utf-8")
    ).hexdigest()


def fingerprint_agent_tool_input(value: Any) -> str:
    """Canonical content fingerprint for trusted runtime tool input.

    Only the digest may enter approval scope/evidence. Runtime integration
    must compute this from the actual validated invocation input and must
    never accept a model-supplied fingerprint as authoritative.
    """

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentHumanApprovalScopeError(
            "Tool input must be canonical JSON."
        ) from exc

    digest = hashlib.sha256()
    digest.update(_AGENT_TOOL_INPUT_HASH_DOMAIN)
    digest.update(encoded)
    return digest.hexdigest()


def _digest(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise AgentHumanApprovalScopeError(
            f"{field_name} must be a SHA-256 digest."
        )
    return value.lower()


def _identifier(
    value: str,
    field_name: str,
    *,
    max_length: int = 128,
) -> str:
    if not isinstance(value, str):
        raise AgentHumanApprovalScopeError(
            f"{field_name} must be a string."
        )
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > max_length
        or not _IDENTIFIER.fullmatch(normalized)
    ):
        raise AgentHumanApprovalScopeError(
            f"Invalid {field_name}."
        )
    return normalized


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise AgentHumanApprovalStateError(
            f"{field_name} must be a datetime."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AgentHumanApprovalError(ValueError):
    pass


class AgentHumanApprovalScopeError(AgentHumanApprovalError):
    pass


class AgentHumanApprovalStateError(AgentHumanApprovalError):
    pass


@dataclass(frozen=True)
class AgentToolApprovalScope:
    """Exact, content-free scope for one governed tool execution attempt.

    The scope contains identifiers and cryptographic fingerprints only.
    Raw tool input, prompts, responses, credentials and secret values must
    never be copied into the Policy Approval subject payload.
    """

    execution_id: str
    governed_tool_id: str
    action_id: str
    profile_id: str
    profile_fingerprint: str
    before_tool_decision_fingerprint: str
    tool_binding_fingerprint: str
    runtime_layer_fingerprint: str
    input_fingerprint: str
    policy_scope: PolicyApprovalScope

    def __post_init__(self) -> None:
        execution_id = _identifier(
            self.execution_id,
            "execution_id",
            max_length=128,
        )
        governed_tool_id = _identifier(
            self.governed_tool_id,
            "governed_tool_id",
        )
        action_id = _identifier(
            self.action_id,
            "action_id",
        )
        profile_id = _identifier(
            self.profile_id,
            "profile_id",
            max_length=64,
        )
        profile_fingerprint = _digest(
            self.profile_fingerprint,
            "profile_fingerprint",
        )
        before_fingerprint = _digest(
            self.before_tool_decision_fingerprint,
            "before_tool_decision_fingerprint",
        )
        binding_fingerprint = _digest(
            self.tool_binding_fingerprint,
            "tool_binding_fingerprint",
        )
        runtime_fingerprint = _digest(
            self.runtime_layer_fingerprint,
            "runtime_layer_fingerprint",
        )
        input_fingerprint = _digest(
            self.input_fingerprint,
            "input_fingerprint",
        )

        if not isinstance(self.policy_scope, PolicyApprovalScope):
            raise AgentHumanApprovalScopeError(
                "policy_scope must be PolicyApprovalScope."
            )

        if self.policy_scope.workspace_id is None:
            raise AgentHumanApprovalScopeError(
                "Agent tool approval requires a Workspace."
            )

        workspace_id = _identifier(
            self.policy_scope.workspace_id,
            "workspace_id",
            max_length=64,
        )
        if self.policy_scope.workspace_id != workspace_id:
            raise AgentHumanApprovalScopeError(
                "Policy Approval Workspace must be normalized."
            )

        if (
            self.policy_scope.subject_type
            != AGENT_HUMAN_APPROVAL_SUBJECT_TYPE
        ):
            raise AgentHumanApprovalScopeError(
                "Unexpected Policy Approval subject_type."
            )

        if self.policy_scope.subject_id != execution_id:
            raise AgentHumanApprovalScopeError(
                "Policy Approval subject_id does not match execution_id."
            )

        expected_payload = {
            "schema_version": AGENT_HUMAN_APPROVAL_SCHEMA_VERSION,
            "governed_tool_id": governed_tool_id,
            "action_id": action_id,
            "profile_id": profile_id,
            "profile_fingerprint": profile_fingerprint,
            "before_tool_decision_fingerprint": before_fingerprint,
            "tool_binding_fingerprint": binding_fingerprint,
            "runtime_layer_fingerprint": runtime_fingerprint,
            "runtime_operation": self.policy_scope.operation.value,
            "input_fingerprint": input_fingerprint,
        }

        if dict(self.policy_scope.subject_payload) != expected_payload:
            raise AgentHumanApprovalScopeError(
                "Policy Approval subject payload does not match "
                "the exact agent tool execution scope."
            )

        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(
            self,
            "governed_tool_id",
            governed_tool_id,
        )
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(
            self,
            "profile_fingerprint",
            profile_fingerprint,
        )
        object.__setattr__(
            self,
            "before_tool_decision_fingerprint",
            before_fingerprint,
        )
        object.__setattr__(
            self,
            "tool_binding_fingerprint",
            binding_fingerprint,
        )
        object.__setattr__(
            self,
            "runtime_layer_fingerprint",
            runtime_fingerprint,
        )
        object.__setattr__(
            self,
            "input_fingerprint",
            input_fingerprint,
        )

    @classmethod
    def from_enforcement(
        cls,
        *,
        decision: AgentBeforeToolExecutionDecision,
        binding: TrustedAgentToolBinding,
        execution_id: str,
        input_fingerprint: str,
    ) -> "AgentToolApprovalScope":
        if not isinstance(
            decision,
            AgentBeforeToolExecutionDecision,
        ):
            raise AgentHumanApprovalScopeError(
                "decision must be AgentBeforeToolExecutionDecision."
            )
        if not isinstance(binding, TrustedAgentToolBinding):
            raise AgentHumanApprovalScopeError(
                "binding must be TrustedAgentToolBinding."
            )

        if decision.workspace_id is None:
            raise AgentHumanApprovalScopeError(
                "Agent approval requires a Workspace."
            )

        if (
            decision.action == AgentEnforcementAction.DENY
            or not decision.approval_required
        ):
            raise AgentHumanApprovalScopeError(
                "The pre-approval enforcement decision does not "
                "require Human Control approval."
            )

        human_layers = [
            item
            for item in decision.layer_decisions
            if item.layer == AgentEnforcementLayer.HUMAN_CONTROL
        ]
        if human_layers:
            raise AgentHumanApprovalScopeError(
                "Pre-approval decision cannot contain a Human Control "
                "layer decision."
            )

        runtime_layers = [
            item
            for item in decision.layer_decisions
            if item.layer == AgentEnforcementLayer.RUNTIME_POLICY
        ]
        if len(runtime_layers) != 1:
            raise AgentHumanApprovalScopeError(
                "Exactly one Runtime Policy layer decision is required."
            )
        runtime_layer = runtime_layers[0]

        request = decision.profile_decision.request
        workspace_id = _identifier(
            decision.workspace_id,
            "workspace_id",
            max_length=64,
        )

        if request.workspace_id != workspace_id:
            raise AgentHumanApprovalScopeError(
                "Agent request Workspace does not match enforcement "
                "Workspace."
            )

        if (
            decision.expected_profile_fingerprint
            != decision.profile_decision.profile_fingerprint
        ):
            raise AgentHumanApprovalScopeError(
                "Selected profile fingerprint does not match the "
                "profile decision."
            )

        governed_tool_id = _identifier(
            binding.governed_tool_id,
            "binding.governed_tool_id",
        )
        action_id = _identifier(
            binding.action_id,
            "binding.action_id",
        )

        if governed_tool_id != request.tool_id:
            raise AgentHumanApprovalScopeError(
                "Trusted tool binding does not match governed tool_id."
            )
        if action_id != request.action_id:
            raise AgentHumanApprovalScopeError(
                "Trusted tool binding does not match action_id."
            )

        binding_workspace = (
            None
            if binding.workspace_id is None
            else _identifier(
                binding.workspace_id,
                "binding.workspace_id",
                max_length=64,
            )
        )
        if binding_workspace not in {None, workspace_id}:
            raise AgentHumanApprovalScopeError(
                "Trusted tool binding belongs to another Workspace."
            )

        if set(binding.capabilities) != set(request.capabilities):
            raise AgentHumanApprovalScopeError(
                "Trusted tool binding capabilities do not match "
                "the governed AgentToolRequest."
            )

        normalized_execution_id = _identifier(
            execution_id,
            "execution_id",
            max_length=128,
        )
        normalized_input_fingerprint = _digest(
            input_fingerprint,
            "input_fingerprint",
        )

        profile_fingerprint = _digest(
            decision.profile_decision.profile_fingerprint,
            "profile_fingerprint",
        )
        before_fingerprint = _digest(
            decision.fingerprint,
            "before_tool_decision_fingerprint",
        )
        binding_fingerprint = _digest(
            binding.fingerprint,
            "tool_binding_fingerprint",
        )
        runtime_layer_fingerprint = _digest(
            runtime_layer.fingerprint,
            "runtime_layer_fingerprint",
        )

        subject_payload = {
            "schema_version": AGENT_HUMAN_APPROVAL_SCHEMA_VERSION,
            "governed_tool_id": governed_tool_id,
            "action_id": action_id,
            "profile_id": decision.profile_decision.profile_id,
            "profile_fingerprint": profile_fingerprint,
            "before_tool_decision_fingerprint": before_fingerprint,
            "tool_binding_fingerprint": binding_fingerprint,
            "runtime_layer_fingerprint": runtime_layer_fingerprint,
            "runtime_operation": binding.runtime_operation.value,
            "input_fingerprint": normalized_input_fingerprint,
        }

        try:
            policy_scope = PolicyApprovalScope(
                workspace_id=workspace_id,
                operation=binding.runtime_operation,
                policy_version=runtime_layer.policy_version,
                policy_fingerprint=runtime_layer.upstream_fingerprint,
                subject_type=AGENT_HUMAN_APPROVAL_SUBJECT_TYPE,
                subject_id=normalized_execution_id,
                subject_payload=subject_payload,
            )
        except ValueError as exc:
            raise AgentHumanApprovalScopeError(str(exc)) from exc

        return cls(
            execution_id=normalized_execution_id,
            governed_tool_id=governed_tool_id,
            action_id=action_id,
            profile_id=decision.profile_decision.profile_id,
            profile_fingerprint=profile_fingerprint,
            before_tool_decision_fingerprint=before_fingerprint,
            tool_binding_fingerprint=binding_fingerprint,
            runtime_layer_fingerprint=runtime_layer_fingerprint,
            input_fingerprint=normalized_input_fingerprint,
            policy_scope=policy_scope,
        )

    @property
    def workspace_id(self) -> str:
        workspace_id = self.policy_scope.workspace_id
        if workspace_id is None:
            raise AgentHumanApprovalScopeError(
                "Agent tool approval Workspace is missing."
            )
        return workspace_id

    @property
    def fingerprint(self) -> str:
        return self.policy_scope.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "governed_tool_id": self.governed_tool_id,
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "before_tool_decision_fingerprint": (
                self.before_tool_decision_fingerprint
            ),
            "tool_binding_fingerprint": self.tool_binding_fingerprint,
            "runtime_layer_fingerprint": (
                self.runtime_layer_fingerprint
            ),
            "input_fingerprint": self.input_fingerprint,
            "policy_scope": self.policy_scope.to_dict(),
            "scope_fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class AgentHumanApprovalEvidence:
    """Content-free evidence of an atomically consumed exact approval."""

    approval_id: str
    workspace_id: str
    scope_fingerprint: str
    policy_fingerprint: str
    before_tool_decision_fingerprint: str
    tool_binding_fingerprint: str
    runtime_layer_fingerprint: str
    input_fingerprint: str
    consumed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approval_id",
            _identifier(
                self.approval_id,
                "approval_id",
                max_length=64,
            ),
        )
        object.__setattr__(
            self,
            "workspace_id",
            _identifier(
                self.workspace_id,
                "workspace_id",
                max_length=64,
            ),
        )

        for field_name in (
            "scope_fingerprint",
            "policy_fingerprint",
            "before_tool_decision_fingerprint",
            "tool_binding_fingerprint",
            "runtime_layer_fingerprint",
            "input_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest(
                    getattr(self, field_name),
                    field_name,
                ),
            )

        object.__setattr__(
            self,
            "consumed_at",
            _aware(self.consumed_at, "consumed_at"),
        )

    @classmethod
    def from_consumed(
        cls,
        *,
        record: PolicyApprovalRecord,
        expected_scope: AgentToolApprovalScope,
    ) -> "AgentHumanApprovalEvidence":
        if not isinstance(record, PolicyApprovalRecord):
            raise AgentHumanApprovalStateError(
                "record must be PolicyApprovalRecord."
            )
        if not isinstance(expected_scope, AgentToolApprovalScope):
            raise AgentHumanApprovalScopeError(
                "expected_scope must be AgentToolApprovalScope."
            )
        if record.status != PolicyApprovalStatus.CONSUMED:
            raise AgentHumanApprovalStateError(
                "Human Control ALLOW requires an atomically consumed "
                "Policy Approval."
            )
        if record.consumed_at is None:
            raise AgentHumanApprovalStateError(
                "Consumed Policy Approval is missing consumed_at."
            )
        if record.token_hash is None:
            raise AgentHumanApprovalStateError(
                "Consumed Policy Approval is missing token proof."
            )
        if record.decided_by is None or record.decided_at is None:
            raise AgentHumanApprovalStateError(
                "Consumed Policy Approval is missing Human Control "
                "decision evidence."
            )

        decided_at = _aware(record.decided_at, "decided_at")
        consumed_at = _aware(record.consumed_at, "consumed_at")
        if consumed_at < decided_at:
            raise AgentHumanApprovalStateError(
                "Policy Approval was consumed before it was approved."
            )

        try:
            PolicyApprovalCore.validate_scope(
                record,
                expected_scope.policy_scope,
            )
        except PolicyApprovalScopeError as exc:
            raise AgentHumanApprovalScopeError(str(exc)) from exc

        return cls(
            approval_id=record.id,
            workspace_id=expected_scope.workspace_id,
            scope_fingerprint=record.scope_fingerprint,
            policy_fingerprint=record.scope.policy_fingerprint,
            before_tool_decision_fingerprint=(
                expected_scope.before_tool_decision_fingerprint
            ),
            tool_binding_fingerprint=(
                expected_scope.tool_binding_fingerprint
            ),
            runtime_layer_fingerprint=(
                expected_scope.runtime_layer_fingerprint
            ),
            input_fingerprint=expected_scope.input_fingerprint,
            consumed_at=record.consumed_at,
        )

    def validate_scope(
        self,
        expected_scope: AgentToolApprovalScope,
    ) -> None:
        if self.workspace_id != expected_scope.workspace_id:
            raise AgentHumanApprovalScopeError(
                "Human approval evidence belongs to another Workspace."
            )

        comparisons = (
            (
                self.scope_fingerprint,
                expected_scope.fingerprint,
                "Policy Approval scope fingerprint changed.",
            ),
            (
                self.policy_fingerprint,
                expected_scope.policy_scope.policy_fingerprint,
                "Runtime Policy fingerprint changed.",
            ),
            (
                self.before_tool_decision_fingerprint,
                expected_scope.before_tool_decision_fingerprint,
                "Before-tool enforcement decision changed.",
            ),
            (
                self.tool_binding_fingerprint,
                expected_scope.tool_binding_fingerprint,
                "Trusted tool binding changed.",
            ),
            (
                self.runtime_layer_fingerprint,
                expected_scope.runtime_layer_fingerprint,
                "Runtime Policy layer decision changed.",
            ),
            (
                self.input_fingerprint,
                expected_scope.input_fingerprint,
                "Tool input fingerprint changed.",
            ),
        )

        for actual, expected, message in comparisons:
            if not hmac.compare_digest(actual, expected):
                raise AgentHumanApprovalScopeError(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "workspace_id": self.workspace_id,
            "scope_fingerprint": self.scope_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "before_tool_decision_fingerprint": (
                self.before_tool_decision_fingerprint
            ),
            "tool_binding_fingerprint": (
                self.tool_binding_fingerprint
            ),
            "runtime_layer_fingerprint": (
                self.runtime_layer_fingerprint
            ),
            "input_fingerprint": self.input_fingerprint,
            "consumed_at": self.consumed_at.isoformat(),
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(
            {
                "schema_version": AGENT_HUMAN_APPROVAL_SCHEMA_VERSION,
                "evidence": self.to_dict(),
            }
        )


class AgentHumanApprovalContract:
    """Trusted bridge from consumed Policy Approval to Human Control ALLOW.

    APPROVED alone is never sufficient. The caller must first atomically
    consume the exact Policy Approval using PolicyApprovalService.consume().
    Only the returned CONSUMED record may become a Human Control ALLOW layer.

    Runtime wiring must never accept AgentEnforcementLayerDecision supplied
    by an agent/model/caller as Human Control authority.
    """

    @staticmethod
    def build_scope(
        *,
        decision: AgentBeforeToolExecutionDecision,
        binding: TrustedAgentToolBinding,
        execution_id: str,
        input_fingerprint: str,
    ) -> AgentToolApprovalScope:
        return AgentToolApprovalScope.from_enforcement(
            decision=decision,
            binding=binding,
            execution_id=execution_id,
            input_fingerprint=input_fingerprint,
        )

    @staticmethod
    def evidence_from_consumed(
        *,
        record: PolicyApprovalRecord,
        expected_scope: AgentToolApprovalScope,
    ) -> AgentHumanApprovalEvidence:
        return AgentHumanApprovalEvidence.from_consumed(
            record=record,
            expected_scope=expected_scope,
        )

    @staticmethod
    def allow_decision(
        *,
        record: PolicyApprovalRecord,
        expected_scope: AgentToolApprovalScope,
    ) -> AgentEnforcementLayerDecision:
        """Build Human Control ALLOW only from a consumed exact approval.

        Runtime wiring must pass the PolicyApprovalRecord returned from the
        successful PolicyApprovalService.consume() call in the same trusted
        execution path. APPROVED, caller-constructed evidence, stale records,
        and mismatched scopes are not authorization.
        """

        evidence = AgentHumanApprovalEvidence.from_consumed(
            record=record,
            expected_scope=expected_scope,
        )

        return AgentEnforcementLayerDecision(
            layer=AgentEnforcementLayer.HUMAN_CONTROL,
            workspace_id=expected_scope.workspace_id,
            action=AgentEnforcementAction.ALLOW,
            reason_codes=("EXACT_SCOPE_APPROVAL_CONSUMED",),
            policy_version=_HUMAN_CONTROL_POLICY_VERSION,
            upstream_fingerprint=evidence.fingerprint,
        )
