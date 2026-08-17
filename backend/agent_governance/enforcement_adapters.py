from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from backend.agent_governance.core import AgentCapability, AgentToolRequest
from backend.agent_governance.enforcement import (
    AgentEnforcementAction,
    AgentEnforcementLayer,
    AgentEnforcementLayerDecision,
)
from backend.agent_governance.profiles import agent_tool_capability_catalog
from backend.orchestration.models import ToolDefinitionModel
from backend.runtime_policy import (
    PolicyOperation,
    ProviderTrust,
    RuntimePolicyContext,
    RuntimePolicyEngine,
    RuntimeTrust,
)
from backend.services.workspace_policy import EffectiveWorkspacePolicy


AGENT_ENFORCEMENT_ADAPTER_SCHEMA_VERSION = (
    "p3-002.2b-a.trusted-enforcement-adapters"
)
AGENT_POLICY_TOOL_ID_METADATA_KEY = "agent_policy_tool_id"
AGENT_POLICY_ACTION_ID_METADATA_KEY = "agent_policy_action_id"
AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY = "agent_policy_runtime_operation"

_TOOL_REGISTRY_POLICY_VERSION = "p3-002.2b-a.tool-registry-adapter"
_WORKSPACE_POLICY_VERSION = "p3-002.2b-a.workspace-policy-adapter"

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_REGISTRY_REASON = re.compile(r"^[a-z0-9_:-]+$")

_BOUNDARY_CAPABILITIES = frozenset(
    {
        AgentCapability.FILESYSTEM_READ,
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.EXTERNAL_NETWORK,
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, field: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise AgentEnforcementAdapterError(f"{field} must be a string.")
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > max_length
        or not _IDENTIFIER.fullmatch(normalized)
    ):
        raise AgentEnforcementAdapterError(f"Invalid {field}.")
    return normalized


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise AgentEnforcementAdapterError(f"{field} must be an array.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AgentEnforcementAdapterError(
                f"{field} must contain non-empty strings."
            )
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _registry_reason_codes(value: Any) -> tuple[str, ...]:
    raw = _string_list(value, "registry reasons")
    result: list[str] = []
    for reason in raw:
        normalized = reason.strip().lower()
        if not _REGISTRY_REASON.fullmatch(normalized):
            raise AgentEnforcementAdapterError(
                f"Invalid Tool Registry reason: {reason!r}."
            )
        code = normalized.upper()
        if code not in result:
            result.append(code)
    return tuple(result)


class AgentEnforcementAdapterError(ValueError):
    pass


class AgentToolBindingIntegrityError(AgentEnforcementAdapterError):
    pass


class AgentRegistryDecisionIntegrityError(AgentEnforcementAdapterError):
    pass


@dataclass(frozen=True)
class TrustedAgentToolBinding:
    registry_tool_id: str
    registry_tool_key: str
    governed_tool_id: str
    action_id: str
    runtime_operation: PolicyOperation
    capabilities: tuple[AgentCapability, ...]
    workspace_id: str | None
    kind: str
    risk_level: str
    isolation_mode: str
    allow_network: bool
    allow_filesystem_read: bool
    allow_filesystem_write: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_tool_id": self.registry_tool_id,
            "registry_tool_key": self.registry_tool_key,
            "governed_tool_id": self.governed_tool_id,
            "action_id": self.action_id,
            "runtime_operation": self.runtime_operation.value,
            "capabilities": [item.value for item in self.capabilities],
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "risk_level": self.risk_level,
            "isolation_mode": self.isolation_mode,
            "allow_network": self.allow_network,
            "allow_filesystem_read": self.allow_filesystem_read,
            "allow_filesystem_write": self.allow_filesystem_write,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(
            {
                "schema_version": AGENT_ENFORCEMENT_ADAPTER_SCHEMA_VERSION,
                "binding": self.to_dict(),
            }
        )


class TrustedAgentEnforcementAdapter:
    """Trusted bridge from platform-owned metadata into P3-002 enforcement."""

    def __init__(self) -> None:
        self._catalog = agent_tool_capability_catalog()

    def bind_tool(self, tool: ToolDefinitionModel) -> TrustedAgentToolBinding:
        if not isinstance(tool, ToolDefinitionModel):
            raise AgentToolBindingIntegrityError(
                "tool must be a ToolDefinitionModel."
            )
        if not isinstance(tool.id, str) or not tool.id.strip():
            raise AgentToolBindingIntegrityError(
                "Persisted Tool Definition id is required."
            )
        tool_key = _identifier(tool.tool_key, "tool_key", max_length=255)
        metadata = tool.metadata_json
        if not isinstance(metadata, Mapping):
            raise AgentToolBindingIntegrityError(
                "Tool metadata must be an object."
            )

        try:
            governed_tool_id = _identifier(
                metadata.get(AGENT_POLICY_TOOL_ID_METADATA_KEY),
                AGENT_POLICY_TOOL_ID_METADATA_KEY,
            )
            action_id = _identifier(
                metadata.get(AGENT_POLICY_ACTION_ID_METADATA_KEY),
                AGENT_POLICY_ACTION_ID_METADATA_KEY,
            )
        except AgentEnforcementAdapterError as exc:
            raise AgentToolBindingIntegrityError(str(exc)) from exc
        raw_operation = metadata.get(
            AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY
        )
        if not isinstance(raw_operation, str):
            raise AgentToolBindingIntegrityError(
                f"{AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY} is required."
            )
        try:
            runtime_operation = PolicyOperation(raw_operation.strip().lower())
        except ValueError as exc:
            raise AgentToolBindingIntegrityError(
                "Unsupported agent_policy_runtime_operation."
            ) from exc

        canonical = self._catalog.get(governed_tool_id)
        if canonical is None:
            raise AgentToolBindingIntegrityError(
                f"Unknown governed tool id: {governed_tool_id}."
            )
        capabilities = tuple(canonical)

        declared_boundary: set[AgentCapability] = set()
        if bool(tool.allow_network):
            declared_boundary.add(AgentCapability.EXTERNAL_NETWORK)
        if bool(tool.allow_filesystem_read):
            declared_boundary.add(AgentCapability.FILESYSTEM_READ)
        if bool(tool.allow_filesystem_write):
            declared_boundary.add(AgentCapability.FILESYSTEM_WRITE)
        canonical_boundary = set(capabilities).intersection(
            _BOUNDARY_CAPABILITIES
        )
        if declared_boundary != canonical_boundary:
            raise AgentToolBindingIntegrityError(
                "Tool Definition capability flags do not match the governed "
                "tool capability catalog."
            )

        kind = str(tool.kind or "").strip().lower()
        if kind == "http" and AgentCapability.EXTERNAL_NETWORK not in capabilities:
            raise AgentToolBindingIntegrityError(
                "HTTP Tool Definition must bind to an external-network "
                "governed capability."
            )
        if kind == "subprocess" and AgentCapability.CODE_EXECUTION not in capabilities:
            raise AgentToolBindingIntegrityError(
                "Subprocess Tool Definition must bind to a code-execution "
                "governed capability."
            )

        uses_code_execution = AgentCapability.CODE_EXECUTION in capabilities
        if uses_code_execution != (
            runtime_operation == PolicyOperation.CODE_EXECUTION
        ):
            raise AgentToolBindingIntegrityError(
                "Code-execution capability and Runtime Policy operation "
                "must be bound consistently."
            )

        return TrustedAgentToolBinding(
            registry_tool_id=tool.id.strip(),
            registry_tool_key=tool_key,
            governed_tool_id=governed_tool_id,
            action_id=action_id,
            runtime_operation=runtime_operation,
            capabilities=capabilities,
            workspace_id=(
                None
                if tool.workspace_id is None
                else _identifier(
                    tool.workspace_id,
                    "tool.workspace_id",
                    max_length=64,
                )
            ),
            kind=kind,
            risk_level=str(tool.risk_level or "").strip().lower(),
            isolation_mode=str(tool.isolation_mode or "").strip().lower(),
            allow_network=bool(tool.allow_network),
            allow_filesystem_read=bool(tool.allow_filesystem_read),
            allow_filesystem_write=bool(tool.allow_filesystem_write),
        )

    def build_request(
        self,
        *,
        tool: ToolDefinitionModel,
        workspace_policy: EffectiveWorkspacePolicy,
        external_domain: str | None = None,
    ) -> AgentToolRequest:
        binding = self.bind_tool(tool)
        policy = self._require_workspace_policy(workspace_policy)
        workspace_id = _identifier(
            policy.workspace_id,
            "workspace_policy.workspace_id",
            max_length=64,
        )
        if binding.workspace_id not in {None, workspace_id}:
            raise AgentToolBindingIntegrityError(
                "Tool Definition is scoped to a different Workspace."
            )

        uses_network = AgentCapability.EXTERNAL_NETWORK in binding.capabilities
        if uses_network and external_domain is None:
            raise AgentToolBindingIntegrityError(
                "A trusted external_domain is required for a network tool."
            )
        if not uses_network and external_domain is not None:
            raise AgentToolBindingIntegrityError(
                "external_domain cannot add network capability to a "
                "non-network governed tool."
            )

        return AgentToolRequest(
            tool_id=binding.governed_tool_id,
            action_id=binding.action_id,
            data_classification=policy.data_classification,
            capabilities=binding.capabilities,
            external_domain=external_domain,
            workspace_id=workspace_id,
        )

    def tool_registry_decision(
        self,
        *,
        tool: ToolDefinitionModel,
        registry_decision: Mapping[str, Any],
        workspace_id: str,
        agent_id: str | None,
    ) -> AgentEnforcementLayerDecision:
        binding = self.bind_tool(tool)
        if not isinstance(registry_decision, Mapping):
            raise AgentRegistryDecisionIntegrityError(
                "registry_decision must be an object."
            )
        workspace = _identifier(workspace_id, "workspace_id", max_length=64)

        self._expect(registry_decision.get("tool_id"), binding.registry_tool_id, "registry tool_id")
        self._expect(
            str(registry_decision.get("tool_key") or "").strip().lower(),
            binding.registry_tool_key,
            "registry tool_key",
        )
        self._expect(registry_decision.get("workspace_id"), workspace, "registry workspace_id")
        self._expect(registry_decision.get("agent_id"), agent_id, "registry agent_id")
        self._expect(
            str(registry_decision.get("risk_level") or "").strip().lower(),
            binding.risk_level,
            "registry risk_level",
        )
        self._expect(
            str(registry_decision.get("kind") or "").strip().lower(),
            binding.kind,
            "registry kind",
        )
        self._expect(
            str(registry_decision.get("isolation_mode") or "").strip().lower(),
            binding.isolation_mode,
            "registry isolation_mode",
        )

        raw_caps = registry_decision.get("capabilities")
        if not isinstance(raw_caps, Mapping):
            raise AgentRegistryDecisionIntegrityError(
                "registry capabilities must be an object."
            )
        expected_caps = {
            "network": binding.allow_network,
            "filesystem_read": binding.allow_filesystem_read,
            "filesystem_write": binding.allow_filesystem_write,
        }
        actual_caps = {key: raw_caps.get(key) for key in expected_caps}
        if any(not isinstance(value, bool) for value in actual_caps.values()):
            raise AgentRegistryDecisionIntegrityError(
                "registry capabilities must be booleans."
            )
        if actual_caps != expected_caps:
            raise AgentRegistryDecisionIntegrityError(
                "registry capabilities do not match Tool Definition."
            )

        allowed = registry_decision.get("allowed")
        if not isinstance(allowed, bool):
            raise AgentRegistryDecisionIntegrityError(
                "registry allowed must be boolean."
            )
        reasons = _registry_reason_codes(registry_decision.get("reasons", []))
        if allowed and reasons:
            raise AgentRegistryDecisionIntegrityError(
                "Allowed registry decision cannot contain deny reasons."
            )
        if not allowed and not reasons:
            raise AgentRegistryDecisionIntegrityError(
                "Denied registry decision requires reasons."
            )

        explicit_required = (
            bool(tool.requires_explicit_allow)
            or binding.risk_level in {"high", "critical"}
            or binding.kind in {"http", "subprocess"}
            or binding.allow_network
            or binding.allow_filesystem_write
        )
        if registry_decision.get("explicit_allow_required") is not explicit_required:
            raise AgentRegistryDecisionIntegrityError(
                "registry explicit_allow_required is inconsistent."
            )

        matched_ids = _string_list(
            registry_decision.get("matched_permission_ids", []),
            "matched_permission_ids",
        )
        allow_ids = _string_list(
            registry_decision.get("allow_permission_ids", []),
            "allow_permission_ids",
        )
        deny_ids = _string_list(
            registry_decision.get("deny_permission_ids", []),
            "deny_permission_ids",
        )
        matched_set = set(matched_ids)
        if not set(allow_ids).issubset(matched_set):
            raise AgentRegistryDecisionIntegrityError(
                "allow_permission_ids must be matched permissions."
            )
        if not set(deny_ids).issubset(matched_set):
            raise AgentRegistryDecisionIntegrityError(
                "deny_permission_ids must be matched permissions."
            )
        if allowed and explicit_required and not allow_ids:
            raise AgentRegistryDecisionIntegrityError(
                "Explicitly governed registry allow requires allow evidence."
            )

        input_size_hint = registry_decision.get("input_size_hint")
        if (
            not isinstance(input_size_hint, int)
            or isinstance(input_size_hint, bool)
            or input_size_hint < 0
        ):
            raise AgentRegistryDecisionIntegrityError(
                "registry input_size_hint must be a non-negative integer."
            )

        evidence = {
            "schema_version": AGENT_ENFORCEMENT_ADAPTER_SCHEMA_VERSION,
            "source": "tool_registry.evaluate",
            "binding_fingerprint": binding.fingerprint,
            "decision": {
                "allowed": allowed,
                "tool_id": binding.registry_tool_id,
                "tool_key": binding.registry_tool_key,
                "workspace_id": workspace,
                "agent_id": agent_id,
                "risk_level": binding.risk_level,
                "kind": binding.kind,
                "isolation_mode": binding.isolation_mode,
                "explicit_allow_required": explicit_required,
                "matched_permission_ids": sorted(matched_ids),
                "allow_permission_ids": sorted(allow_ids),
                "deny_permission_ids": sorted(deny_ids),
                "reasons": list(reasons),
                "capabilities": expected_caps,
                "input_size_hint": input_size_hint,
            },
        }
        return AgentEnforcementLayerDecision(
            layer=AgentEnforcementLayer.TOOL_REGISTRY,
            workspace_id=workspace,
            action=(
                AgentEnforcementAction.ALLOW
                if allowed
                else AgentEnforcementAction.DENY
            ),
            reason_codes=("ALLOWED",) if allowed else reasons,
            policy_version=_TOOL_REGISTRY_POLICY_VERSION,
            upstream_fingerprint=_sha256(evidence),
        )

    def workspace_policy_decision(
        self,
        *,
        workspace_policy: EffectiveWorkspacePolicy,
        request: AgentToolRequest,
    ) -> AgentEnforcementLayerDecision:
        policy = self._require_workspace_policy(workspace_policy)
        self._assert_request_policy_scope(request, policy)

        network_access = str(policy.network_access).strip().lower()
        filesystem_access = str(policy.filesystem_access).strip().lower()
        if network_access not in {"denied", "restricted", "allowed"}:
            raise AgentEnforcementAdapterError(
                "Unsupported Workspace network_access."
            )
        if filesystem_access not in {"none", "read_only", "read_write"}:
            raise AgentEnforcementAdapterError(
                "Unsupported Workspace filesystem_access."
            )

        capabilities = set(request.capabilities)
        deny_reasons: list[str] = []
        approval_reasons: list[str] = []

        filesystem_read = AgentCapability.FILESYSTEM_READ in capabilities
        filesystem_write = AgentCapability.FILESYSTEM_WRITE in capabilities
        if filesystem_access == "none" and (filesystem_read or filesystem_write):
            deny_reasons.append("FILESYSTEM_DENIED")
        elif filesystem_access == "read_only" and filesystem_write:
            deny_reasons.append("FILESYSTEM_WRITE_DENIED")

        if AgentCapability.EXTERNAL_NETWORK in capabilities:
            if network_access == "denied":
                deny_reasons.append("NETWORK_DENIED")
            elif network_access == "restricted":
                approval_reasons.append("NETWORK_RESTRICTED")

        if deny_reasons:
            action = AgentEnforcementAction.DENY
            reasons = tuple(deny_reasons)
        elif approval_reasons:
            action = AgentEnforcementAction.REQUIRE_APPROVAL
            reasons = tuple(approval_reasons)
        else:
            action = AgentEnforcementAction.ALLOW
            reasons = ("ALLOWED",)

        evidence = {
            "schema_version": AGENT_ENFORCEMENT_ADAPTER_SCHEMA_VERSION,
            "source": "workspace_policy.effective",
            "policy": policy.to_dict(),
            "request": request.to_dict(),
            "action": action.value,
            "reason_codes": list(reasons),
        }
        return AgentEnforcementLayerDecision(
            layer=AgentEnforcementLayer.WORKSPACE_POLICY,
            workspace_id=request.workspace_id or "",
            action=action,
            reason_codes=reasons,
            policy_version=_WORKSPACE_POLICY_VERSION,
            upstream_fingerprint=_sha256(evidence),
        )

    def runtime_policy_decision(
        self,
        *,
        tool: ToolDefinitionModel,
        workspace_policy: EffectiveWorkspacePolicy,
        request: AgentToolRequest,
        runtime_trust: RuntimeTrust | str | None = None,
        contains_secrets: bool = False,
        engine: RuntimePolicyEngine | None = None,
    ) -> AgentEnforcementLayerDecision:
        binding = self.bind_tool(tool)
        policy = self._require_workspace_policy(workspace_policy)
        self._assert_request_policy_scope(request, policy)
        self._assert_request_binding(request, binding)

        if not isinstance(contains_secrets, bool):
            raise AgentEnforcementAdapterError(
                "contains_secrets must be boolean."
            )
        normalized_runtime: RuntimeTrust | None
        if runtime_trust is None:
            normalized_runtime = None
        elif isinstance(runtime_trust, RuntimeTrust):
            normalized_runtime = runtime_trust
        elif isinstance(runtime_trust, str):
            try:
                normalized_runtime = RuntimeTrust(runtime_trust.strip().lower())
            except ValueError as exc:
                raise AgentEnforcementAdapterError(
                    "Unsupported runtime_trust."
                ) from exc
        else:
            raise AgentEnforcementAdapterError("Unsupported runtime_trust.")

        provider_trust: ProviderTrust | None = None
        if binding.runtime_operation == PolicyOperation.MODEL_INFERENCE:
            provider_trust = policy.provider_trust_for(policy.provider)

        context = RuntimePolicyContext(
            operation=binding.runtime_operation,
            data_classification=request.data_classification,
            workspace_id=request.workspace_id,
            provider_trust=provider_trust,
            runtime_trust=normalized_runtime,
            contains_secrets=contains_secrets,
            network_requested=(
                AgentCapability.EXTERNAL_NETWORK in request.capabilities
            ),
        )
        decision = (engine or RuntimePolicyEngine()).evaluate(context)
        return AgentEnforcementLayerDecision.from_runtime_policy(
            workspace_id=request.workspace_id or "",
            decision=decision,
        )

    @staticmethod
    def _require_workspace_policy(
        value: EffectiveWorkspacePolicy,
    ) -> EffectiveWorkspacePolicy:
        if not isinstance(value, EffectiveWorkspacePolicy):
            raise AgentEnforcementAdapterError(
                "workspace_policy must be EffectiveWorkspacePolicy."
            )
        return value

    @staticmethod
    def _expect(actual: Any, expected: Any, field: str) -> None:
        if actual != expected:
            raise AgentRegistryDecisionIntegrityError(
                f"{field} does not match trusted Tool Registry state."
            )

    @staticmethod
    def _assert_request_policy_scope(
        request: AgentToolRequest,
        policy: EffectiveWorkspacePolicy,
    ) -> None:
        if not isinstance(request, AgentToolRequest):
            raise AgentEnforcementAdapterError(
                "request must be AgentToolRequest."
            )
        workspace = _identifier(
            policy.workspace_id,
            "workspace_policy.workspace_id",
            max_length=64,
        )
        if request.workspace_id != workspace:
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest Workspace does not match Workspace Policy."
            )
        if request.data_classification != policy.data_classification:
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest classification does not match Workspace Policy."
            )

    @staticmethod
    def _assert_request_binding(
        request: AgentToolRequest,
        binding: TrustedAgentToolBinding,
    ) -> None:
        if request.tool_id != binding.governed_tool_id:
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest tool_id does not match trusted binding."
            )
        if request.action_id != binding.action_id:
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest action_id does not match trusted binding."
            )
        if tuple(request.capabilities) != tuple(binding.capabilities):
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest capabilities do not match trusted binding."
            )
        if binding.workspace_id not in {None, request.workspace_id}:
            raise AgentToolBindingIntegrityError(
                "AgentToolRequest uses a Tool Definition from another Workspace."
            )


__all__ = [
    "AGENT_ENFORCEMENT_ADAPTER_SCHEMA_VERSION",
    "AGENT_POLICY_ACTION_ID_METADATA_KEY",
    "AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY",
    "AGENT_POLICY_TOOL_ID_METADATA_KEY",
    "AgentEnforcementAdapterError",
    "AgentRegistryDecisionIntegrityError",
    "AgentToolBindingIntegrityError",
    "TrustedAgentEnforcementAdapter",
    "TrustedAgentToolBinding",
]
