from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.agent_governance.approval import (
    AgentHumanApprovalContract,
    AgentHumanApprovalError,
    fingerprint_agent_tool_input,
)
from backend.agent_governance.enforcement import (
    AgentEnforcementAction,
)
from backend.agent_governance.runtime import (
    AgentToolRuntimeGovernance,
    AgentToolRuntimeGovernanceError,
)

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.enums import ExecutionStepType
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.runtime import (
    ExecutionPlanRuntime,
    StepExecutionContext,
    StepExecutionResult,
)
from backend.orchestration.tool_schemas import DirectToolExecutionRequest
from backend.orchestration.tools import (
    ToolNotFound,
    ToolPermissionDenied,
    ToolRegistryService,
    ToolRepository,
)
from backend.policy_approvals.core import (
    PolicyApprovalScopeError,
    PolicyApprovalStateError,
    PolicyApprovalTokenError,
)
from backend.policy_approvals.service import (
    PolicyApprovalNotFoundError,
    PolicyApprovalService,
    PolicyApprovalWorkspaceError,
)
from backend.secrets.injection import SecretLeaseAccessor, build_secret_accessor
from backend.secrets.schemas import SecretResolveContext
from backend.secrets.service import SecretManagerService


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


_AGENT_TOOL_APPROVAL_REQUEST_SCHEMA_VERSION = (
    "p3-002.2b-c2.1.direct-approval-request"
)
_AGENT_TOOL_APPROVAL_CONSUME_SCHEMA_VERSION = (
    "p3-002.2b-c2.2.direct-approval-consume"
)


class ToolExecutionError(RuntimeError):
    pass


class ToolApprovalRequired(ToolExecutionError):
    'Typed direct-execution approval precondition.'

    def __init__(
        self,
        *,
        execution_id: str,
        approval_id: str,
        scope_fingerprint: str,
        expires_at: str,
        reason_codes: tuple[str, ...],
        created: bool,
    ) -> None:
        super().__init__("Human Control approval is required.")
        self.execution_id = execution_id
        self.approval_id = approval_id
        self.scope_fingerprint = scope_fingerprint
        self.expires_at = expires_at
        self.reason_codes = tuple(reason_codes)
        self.created = bool(created)

    def to_detail(self) -> dict[str, Any]:
        return {
            "schema_version": (
                _AGENT_TOOL_APPROVAL_REQUEST_SCHEMA_VERSION
            ),
            "code": "AGENT_TOOL_APPROVAL_REQUIRED",
            "execution_id": self.execution_id,
            "approval_id": self.approval_id,
            "scope_fingerprint": self.scope_fingerprint,
            "expires_at": self.expires_at,
            "reason_codes": list(self.reason_codes),
            "created": self.created,
        }


class ToolApprovalCredentialError(ToolExecutionError):
    pass


class ToolApprovalConflictError(ToolExecutionError):
    pass


class ToolSchemaValidationError(ToolExecutionError):
    pass


class ToolPayloadLimitError(ToolExecutionError):
    pass


@dataclass(frozen=True)
class ToolExecutionContext:
    invocation_id: str
    tool_id: str
    tool_key: str
    handler_ref: str
    workspace_id: str | None
    agent_id: str | None
    plan_id: str | None
    step_id: str | None
    correlation_id: str | None
    input: dict[str, Any]
    isolation_mode: str
    capabilities: dict[str, bool]
    metadata: dict[str, Any]
    secret_accessor: SecretLeaseAccessor | None = None

    async def secret(self, alias: str) -> str:
        if self.secret_accessor is None:
            raise ToolExecutionError(
                "Tool has no authorized secret bindings for this invocation."
            )
        return await self.secret_accessor.get(alias)


ToolHandler = Callable[
    [ToolExecutionContext],
    Awaitable[dict[str, Any]],
]


class ToolHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        handler_ref: str,
        handler: ToolHandler,
        *,
        replace: bool = False,
    ) -> None:
        key = self._normalize(handler_ref)
        if key in self._handlers and not replace:
            raise ValueError(f"Tool handler уже зарегистрирован: {key}.")
        self._handlers[key] = handler

    def unregister(self, handler_ref: str) -> bool:
        return self._handlers.pop(self._normalize(handler_ref), None) is not None

    def get(self, handler_ref: str) -> ToolHandler:
        key = self._normalize(handler_ref)
        handler = self._handlers.get(key)
        if handler is None:
            raise ToolExecutionError(
                f"Tool handler не зарегистрирован: {handler_ref}."
            )
        return handler

    def keys(self) -> list[str]:
        return sorted(self._handlers)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("handler_ref не может быть пустым.")
        return normalized


class JsonSchemaValidator:
    """Small deterministic subset of JSON Schema used at tool boundaries."""

    @classmethod
    def validate(
        cls,
        value: Any,
        schema: dict[str, Any],
        *,
        path: str = "$",
    ) -> None:
        if not schema:
            return

        expected = schema.get("type")
        if expected is not None and not cls._matches_type(value, expected):
            raise ToolSchemaValidationError(
                f"{path}: ожидался JSON type {expected}."
            )

        if "enum" in schema and value not in schema["enum"]:
            raise ToolSchemaValidationError(
                f"{path}: значение отсутствует в enum."
            )

        if isinstance(value, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    raise ToolSchemaValidationError(
                        f"{path}.{key}: обязательное поле отсутствует."
                    )

            properties = schema.get("properties", {})
            for key, child in properties.items():
                if key in value and isinstance(child, dict):
                    cls.validate(value[key], child, path=f"{path}.{key}")

            if schema.get("additionalProperties") is False:
                unexpected = set(value) - set(properties)
                if unexpected:
                    raise ToolSchemaValidationError(
                        f"{path}: неизвестные поля: {sorted(unexpected)}."
                    )

        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                cls.validate(
                    item,
                    schema["items"],
                    path=f"{path}[{index}]",
                )

        if isinstance(value, str):
            if len(value) < int(schema.get("minLength", 0)):
                raise ToolSchemaValidationError(
                    f"{path}: строка короче minLength."
                )
            maximum = schema.get("maxLength")
            if maximum is not None and len(value) > int(maximum):
                raise ToolSchemaValidationError(
                    f"{path}: строка длиннее maxLength."
                )

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None and value < minimum:
                raise ToolSchemaValidationError(
                    f"{path}: значение меньше minimum."
                )
            if maximum is not None and value > maximum:
                raise ToolSchemaValidationError(
                    f"{path}: значение больше maximum."
                )

    @staticmethod
    def _matches_type(value: Any, expected: str | list[str]) -> bool:
        expected_types = [expected] if isinstance(expected, str) else expected
        type_checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "number": lambda item: (
                isinstance(item, (int, float)) and not isinstance(item, bool)
            ),
            "integer": lambda item: (
                isinstance(item, int) and not isinstance(item, bool)
            ),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        return any(
            type_checks.get(name, lambda item: True)(value)
            for name in expected_types
        )


class ToolSandbox:
    """
    Restricted application sandbox.

    Handlers receive only immutable identifiers, declared capabilities and a
    JSON round-tripped input object. No Session, AppContainer, credentials or
    filesystem object is inherited. This is a strong application boundary,
    not an operating-system security boundary.
    """

    def __init__(self) -> None:
        self._semaphores: dict[str, tuple[int, asyncio.Semaphore]] = {}

    @classmethod
    def validate_input(
        cls,
        *,
        tool: Any,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        'Canonicalize and validate input at the real tool boundary.'

        clean_input, input_size = cls._json_round_trip(input_data)

        if input_size > tool.max_input_bytes:
            raise ToolPayloadLimitError(
                "Tool input превышает max_input_bytes: "
                f"{input_size} > {tool.max_input_bytes}."
            )

        JsonSchemaValidator.validate(
            clean_input,
            tool.input_schema_json,
        )

        if not isinstance(clean_input, dict):
            raise ToolSchemaValidationError(
                "Tool input boundary requires an object."
            )

        return clean_input

    async def execute(
        self,
        *,
        tool: Any,
        handler: ToolHandler,
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        clean_input = self.validate_input(
            tool=tool,
            input_data=context.input,
        )

        safe_context = ToolExecutionContext(
            invocation_id=context.invocation_id,
            tool_id=context.tool_id,
            tool_key=context.tool_key,
            handler_ref=context.handler_ref,
            workspace_id=context.workspace_id,
            agent_id=context.agent_id,
            plan_id=context.plan_id,
            step_id=context.step_id,
            correlation_id=context.correlation_id,
            input=clean_input,
            isolation_mode=context.isolation_mode,
            capabilities=deepcopy(context.capabilities),
            metadata=deepcopy(context.metadata),
            secret_accessor=context.secret_accessor,
        )

        semaphore = self._semaphore(tool.id, tool.max_concurrency)
        async with semaphore:
            async with asyncio.timeout(tool.timeout_seconds):
                output = await handler(safe_context)

        if not isinstance(output, dict):
            raise ToolExecutionError("Tool handler должен вернуть object.")

        clean_output, output_size = self._json_round_trip(output)
        if output_size > tool.max_output_bytes:
            raise ToolPayloadLimitError(
                "Tool output превышает max_output_bytes: "
                f"{output_size} > {tool.max_output_bytes}."
            )

        JsonSchemaValidator.validate(clean_output, tool.output_schema_json)
        return clean_output

    def _semaphore(
        self,
        tool_id: str,
        capacity: int,
    ) -> asyncio.Semaphore:
        existing = self._semaphores.get(tool_id)
        if existing is not None and existing[0] == capacity:
            return existing[1]
        semaphore = asyncio.Semaphore(max(capacity, 1))
        self._semaphores[tool_id] = (capacity, semaphore)
        return semaphore

    @staticmethod
    def _json_round_trip(value: Any) -> tuple[Any, int]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolSchemaValidationError(
                "Tool payload должен быть валидным JSON."
            ) from exc
        return json.loads(encoded.decode("utf-8")), len(encoded)


class ToolExecutionRuntime:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
        secret_manager: SecretManagerService | None = None,
        agent_governance: AgentToolRuntimeGovernance | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._secret_manager = secret_manager
        self._agent_governance = agent_governance
        self._handlers = ToolHandlerRegistry()
        self._sandbox = ToolSandbox()
        self._started = 0
        self._completed = 0
        self._failed = 0
        self._denied = 0
        self._timed_out = 0
        self._active: set[str] = set()
        self._register_defaults()

    @property
    def handlers(self) -> ToolHandlerRegistry:
        return self._handlers

    def stats(self) -> dict[str, Any]:
        return {
            "active_invocation_ids": sorted(self._active),
            "active_count": len(self._active),
            "started": self._started,
            "completed": self._completed,
            "failed": self._failed,
            "denied": self._denied,
            "timed_out": self._timed_out,
            "registered_handlers": self._handlers.keys(),
            "agent_governance_enabled": (
                self._agent_governance is not None
            ),
            "isolation": {
                "mode": "application_restricted",
                "os_process_boundary": False,
                "json_boundary": True,
                "timeout_enforced": True,
                "payload_limits_enforced": True,
                "capability_declaration_enforced": True,
                "ephemeral_secret_leases": self._secret_manager is not None,
                "secret_values_persisted_in_payload": False,
            },
        }

    async def execute_step(
        self,
        context: StepExecutionContext,
    ) -> StepExecutionResult:
        with self._session_factory() as session:
            step = ExecutionPlanRepository(session).get_step(
                context.plan_id,
                context.step_id,
            )
            if step is None or not step.tool_name:
                raise ToolNotFound("Tool Step или tool_name не найден.")
            tool_key = step.tool_name.strip().lower()

        output, invocation_id = await self._execute(
            tool_key=tool_key,
            workspace_id=context.workspace_id,
            agent_id=context.agent_id,
            plan_id=context.plan_id,
            step_id=context.step_id,
            correlation_id=context.plan_id,
            input_data=context.input,
        )
        return StepExecutionResult(
            output=output,
            metadata={"tool_invocation_id": invocation_id},
        )

    async def execute_direct(
        self,
        tool_id: str,
        request: DirectToolExecutionRequest,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            tool = ToolRepository(session).get(tool_id)
            if tool is None:
                return None
            tool_key = tool.tool_key

        output, invocation_id = await self._execute(
            tool_key=tool_key,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            plan_id=None,
            step_id=None,
            correlation_id=request.correlation_id,
            input_data=request.input,
            approval_execution_id=(
                request.approval_execution_id
            ),
            approval_id=request.approval_id,
            approval_token=request.approval_token,
            exact_tool_id=tool_id,
        )
        return {
            "invocation_id": invocation_id,
            "tool_id": tool_id,
            "tool_key": tool_key,
            "output": output,
        }

    async def shutdown(self) -> None:
        self._active.clear()

    async def _execute(
        self,
        *,
        tool_key: str,
        workspace_id: str | None,
        agent_id: str | None,
        plan_id: str | None,
        step_id: str | None,
        correlation_id: str | None,
        input_data: dict[str, Any],
        approval_execution_id: str | None = None,
        approval_id: str | None = None,
        approval_token: str | None = None,
        exact_tool_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        approval_values = (
            approval_execution_id,
            approval_id,
            approval_token,
        )
        approval_any = any(
            value is not None
            for value in approval_values
        )
        approval_bundle_present = all(
            isinstance(value, str) and bool(value)
            for value in approval_values
        )

        if approval_any and not approval_bundle_present:
            raise ToolApprovalCredentialError(
                "Incomplete Human Control approval credentials."
            )

        if approval_bundle_present:
            if (
                len(approval_execution_id or "") > 128
                or len(approval_id or "") > 64
                or not (32 <= len(approval_token or "") <= 1024)
            ):
                raise ToolApprovalCredentialError(
                    "Invalid Human Control approval credential shape."
                )

        if approval_bundle_present and workspace_id is None:
            raise ToolApprovalCredentialError(
                "Approval credentials require a Workspace."
            )

        if approval_bundle_present and (
            exact_tool_id is None
            or plan_id is not None
            or step_id is not None
        ):
            raise ToolApprovalConflictError(
                "Approval resubmission is supported only for "
                "direct exact-tool execution."
            )

        with self._session_factory() as session:
            repository = ToolRepository(session)
            service = ToolRegistryService(repository, self._event_bus)
            tool = (
                repository.get(exact_tool_id)
                if exact_tool_id is not None
                else service.resolve(tool_key, workspace_id)
            )
            if tool is None:
                raise ToolNotFound(f"Tool не зарегистрирован: {tool_key}.")

            registry_policy = service.evaluate(
                tool=tool,
                workspace_id=workspace_id,
                agent_id=agent_id,
                input_data=input_data,
            )

            policy = deepcopy(registry_policy)
            agent_gate = None
            governance_failure = False
            deny_reasons = list(
                registry_policy.get("reasons", [])
            )

            if (
                registry_policy["allowed"]
                and self._agent_governance is not None
            ):
                try:
                    agent_gate = self._agent_governance.evaluate(
                        session=session,
                        tool=tool,
                        registry_decision=registry_policy,
                        workspace_id=workspace_id,
                        agent_id=agent_id,
                        input_data=input_data,
                    )
                except AgentToolRuntimeGovernanceError as exc:
                    governance_failure = True
                    deny_reasons = [
                        "agent_governance_failed_closed"
                    ]
                    policy["agent_governance"] = {
                        "failed_closed": True,
                        "error_type": exc.__class__.__name__,
                    }
                else:
                    if agent_gate is not None:
                        policy["agent_governance"] = (
                            agent_gate.to_policy_dict()
                        )
                        if not agent_gate.decision.executable:
                            deny_reasons = list(
                                agent_gate.decision.reason_codes
                            )

            approval_consumed_event_payload: dict[str, Any] | None = None

            if (
                approval_bundle_present
                and bool(registry_policy["allowed"])
                and not governance_failure
            ):
                if agent_gate is None:
                    raise ToolApprovalConflictError(
                        "Approval credentials do not belong to a "
                        "currently governed tool execution."
                    )

                if (
                    agent_gate.decision.action
                    == AgentEnforcementAction.REQUIRE_APPROVAL
                    and agent_gate.decision.approval_required
                    and not agent_gate.decision.isolation_required
                ):
                    validated_input = self._sandbox.validate_input(
                        tool=tool,
                        input_data=input_data,
                    )
                    validated_input_fingerprint = (
                        fingerprint_agent_tool_input(
                            validated_input
                        )
                    )

                    if (
                        validated_input_fingerprint
                        != agent_gate.input_fingerprint
                    ):
                        raise ToolApprovalConflictError(
                            "Validated tool input fingerprint changed "
                            "during approval resubmission."
                        )

                    try:
                        expected_scope = (
                            AgentHumanApprovalContract.build_scope(
                                decision=agent_gate.decision,
                                binding=agent_gate.binding,
                                execution_id=(
                                    approval_execution_id
                                    or ""
                                ),
                                input_fingerprint=(
                                    validated_input_fingerprint
                                ),
                            )
                        )
                    except AgentHumanApprovalError as exc:
                        raise ToolApprovalConflictError(
                            "Approval scope does not match the "
                            "current tool execution."
                        ) from exc

                    approval_service = PolicyApprovalService(
                        session=session,
                        event_bus=self._event_bus,
                    )

                    try:
                        consumed_record = (
                            await approval_service.consume(
                                approval_id=approval_id or "",
                                workspace_id=workspace_id or "",
                                token=approval_token or "",
                                scope=(
                                    expected_scope.policy_scope
                                ),
                            )
                        )
                    except PolicyApprovalTokenError as exc:
                        raise ToolApprovalCredentialError(
                            "Human Control approval token is invalid."
                        ) from exc
                    except (
                        PolicyApprovalScopeError,
                        PolicyApprovalStateError,
                        PolicyApprovalNotFoundError,
                        PolicyApprovalWorkspaceError,
                    ) as exc:
                        raise ToolApprovalConflictError(
                            "Human Control approval is stale, "
                            "unavailable, or outside the exact scope."
                        ) from exc

                    try:
                        approval_evidence = (
                            AgentHumanApprovalContract
                            .evidence_from_consumed(
                                record=consumed_record,
                                expected_scope=expected_scope,
                            )
                        )

                        if self._agent_governance is None:
                            raise AgentToolRuntimeGovernanceError(
                                "Agent governance disappeared during "
                                "approval finalization."
                            )

                        agent_gate = (
                            self._agent_governance
                            .finalize_with_consumed_approval(
                                gate=agent_gate,
                                record=consumed_record,
                                expected_scope=expected_scope,
                            )
                        )
                    except (
                        AgentHumanApprovalError,
                        AgentToolRuntimeGovernanceError,
                    ) as exc:
                        raise ToolExecutionError(
                            "Consumed Human Control approval "
                            "failed closed during finalization."
                        ) from exc

                    if (
                        not agent_gate.decision.executable
                        or agent_gate.decision.action
                        != AgentEnforcementAction.ALLOW
                        or agent_gate.decision.approval_required
                        or agent_gate.decision.isolation_required
                    ):
                        raise ToolExecutionError(
                            "Human Control approval did not produce "
                            "an executable final decision."
                        )

                    policy["agent_governance"] = (
                        agent_gate.to_policy_dict()
                    )
                    policy["agent_approval"] = {
                        "schema_version": (
                            _AGENT_TOOL_APPROVAL_CONSUME_SCHEMA_VERSION
                        ),
                        "execution_id": (
                            expected_scope.execution_id
                        ),
                        "evidence": (
                            approval_evidence.to_dict()
                        ),
                        "evidence_fingerprint": (
                            approval_evidence.fingerprint
                        ),
                    }

                    approval_consumed_event_payload = {
                        "schema_version": (
                            _AGENT_TOOL_APPROVAL_CONSUME_SCHEMA_VERSION
                        ),
                        "execution_id": (
                            expected_scope.execution_id
                        ),
                        "approval_id": (
                            approval_evidence.approval_id
                        ),
                        "scope_fingerprint": (
                            approval_evidence.scope_fingerprint
                        ),
                        "evidence_fingerprint": (
                            approval_evidence.fingerprint
                        ),
                        "registry_tool_id": (
                            agent_gate.binding.registry_tool_id
                        ),
                        "governed_tool_id": (
                            agent_gate.binding.governed_tool_id
                        ),
                        "action_id": (
                            agent_gate.binding.action_id
                        ),
                        "profile_id": (
                            agent_gate.profile.profile_id
                        ),
                        "profile_fingerprint": (
                            agent_gate.profile.fingerprint
                        ),
                        "binding_fingerprint": (
                            agent_gate.binding.fingerprint
                        ),
                        "input_fingerprint": (
                            agent_gate.input_fingerprint
                        ),
                        "decision_fingerprint": (
                            agent_gate.decision.fingerprint
                        ),
                        "consumed_at": (
                            approval_evidence
                            .consumed_at
                            .isoformat()
                        ),
                    }

                elif (
                    agent_gate.decision.action
                    == AgentEnforcementAction.ALLOW
                ):
                    raise ToolApprovalConflictError(
                        "Approval credentials are stale because "
                        "the current execution no longer requires "
                        "Human Control approval."
                    )
                # DENY / REQUIRE_ISOLATION intentionally fall through.
                # Canonical deny/isolation wins without consuming token.

            approval_required_error: ToolApprovalRequired | None = None

            direct_approval_required = (
                agent_gate is not None
                and not governance_failure
                and bool(registry_policy["allowed"])
                and agent_gate.decision.action
                == AgentEnforcementAction.REQUIRE_APPROVAL
                and agent_gate.decision.approval_required
                and not agent_gate.decision.isolation_required
                and exact_tool_id is not None
                and plan_id is None
                and step_id is None
                and not approval_bundle_present
            )

            if direct_approval_required:
                validated_input = self._sandbox.validate_input(
                    tool=tool,
                    input_data=input_data,
                )

                validated_input_fingerprint = (
                    fingerprint_agent_tool_input(
                        validated_input
                    )
                )

                if (
                    validated_input_fingerprint
                    != agent_gate.input_fingerprint
                ):
                    raise ToolExecutionError(
                        "Validated tool input fingerprint changed "
                        "during approval preparation."
                    )

                execution_id = (
                    "agent_tool_exec_" + uuid4().hex
                )

                try:
                    approval_scope = (
                        AgentHumanApprovalContract.build_scope(
                            decision=agent_gate.decision,
                            binding=agent_gate.binding,
                            execution_id=execution_id,
                            input_fingerprint=(
                                validated_input_fingerprint
                            ),
                        )
                    )

                    approval_result = await PolicyApprovalService(
                        session=session,
                        event_bus=self._event_bus,
                    ).request(
                        scope=approval_scope.policy_scope,
                        reason_codes=(
                            agent_gate.decision.reason_codes
                        ),
                        requested_by="tool_execution_runtime",
                        metadata={
                            "schema_version": (
                                _AGENT_TOOL_APPROVAL_REQUEST_SCHEMA_VERSION
                            ),
                            "source": "tool_execution_runtime",
                        },
                    )
                except (AgentHumanApprovalError, ValueError) as exc:
                    raise ToolExecutionError(
                        "Agent tool approval request "
                        "failed closed."
                    ) from exc

                approval_required_error = ToolApprovalRequired(
                    execution_id=approval_scope.execution_id,
                    approval_id=approval_result.record.id,
                    scope_fingerprint=(
                        approval_scope.fingerprint
                    ),
                    expires_at=(
                        approval_result.record.expires_at.isoformat()
                    ),
                    reason_codes=(
                        agent_gate.decision.reason_codes
                    ),
                    created=approval_result.created,
                )

            execution_allowed = (
                bool(registry_policy["allowed"])
                and not governance_failure
                and (
                    agent_gate is None
                    or agent_gate.decision.executable
                )
            )
            policy["execution_allowed"] = execution_allowed

            if approval_required_error is not None:
                # PENDING approval is not an execution attempt.
                invocation_id = None
                error = None
            elif not execution_allowed:
                invocation = repository.create_invocation(
                    tool=tool,
                    tool_key=tool.tool_key,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    correlation_id=correlation_id,
                    status="denied",
                    isolation_mode=tool.isolation_mode,
                    input_data=deepcopy(input_data),
                    policy=policy,
                    error="; ".join(deny_reasons),
                )
                invocation_id = invocation.id
                error = (
                    invocation.error
                    or "Tool execution denied."
                )
            else:
                invocation = repository.create_invocation(
                    tool=tool,
                    tool_key=tool.tool_key,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    correlation_id=correlation_id,
                    status="running",
                    isolation_mode=tool.isolation_mode,
                    input_data=deepcopy(input_data),
                    policy=policy,
                )
                invocation_id = invocation.id
                error = None

            tool_snapshot = {
                "id": tool.id,
                "tool_key": tool.tool_key,
                "handler_ref": tool.handler_ref,
                "timeout_seconds": tool.timeout_seconds,
                "max_concurrency": tool.max_concurrency,
                "max_input_bytes": tool.max_input_bytes,
                "max_output_bytes": tool.max_output_bytes,
                "input_schema_json": deepcopy(tool.input_schema_json),
                "output_schema_json": deepcopy(tool.output_schema_json),
                "isolation_mode": tool.isolation_mode,
                "allow_network": tool.allow_network,
                "allow_filesystem_read": tool.allow_filesystem_read,
                "allow_filesystem_write": tool.allow_filesystem_write,
                "metadata_json": deepcopy(tool.metadata_json),
            }

        if approval_consumed_event_payload is not None:
            await self._event_bus.publish(
                Event(
                    event_type="agent.tool.approval.consumed",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload=approval_consumed_event_payload,
                )
            )

        if approval_required_error is not None:
            if agent_gate is None:
                raise ToolExecutionError(
                    "Approval state lost its trusted governance gate."
                )

            await self._event_bus.publish(
                Event(
                    event_type="agent.tool.approval.required",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload={
                        **approval_required_error.to_detail(),
                        "registry_tool_id": (
                            agent_gate.binding.registry_tool_id
                        ),
                        "governed_tool_id": (
                            agent_gate.binding.governed_tool_id
                        ),
                        "action_id": (
                            agent_gate.binding.action_id
                        ),
                        "profile_id": (
                            agent_gate.profile.profile_id
                        ),
                        "profile_fingerprint": (
                            agent_gate.profile.fingerprint
                        ),
                        "binding_fingerprint": (
                            agent_gate.binding.fingerprint
                        ),
                        "input_fingerprint": (
                            agent_gate.input_fingerprint
                        ),
                        "decision_fingerprint": (
                            agent_gate.decision.fingerprint
                        ),
                    },
                )
            )
            raise approval_required_error

        if agent_gate is not None:
            await self._event_bus.publish(
                Event(
                    event_type="agent.tool.enforcement.evaluated",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload=agent_gate.event_payload(
                        invocation_id=invocation_id
                    ),
                )
            )
        elif governance_failure:
            await self._event_bus.publish(
                Event(
                    event_type="agent.tool.enforcement.failed_closed",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload={
                        "invocation_id": invocation_id,
                        "tool_id": tool_snapshot["id"],
                        "tool_key": tool_snapshot["tool_key"],
                        "reason_codes": [
                            "AGENT_GOVERNANCE_FAILED_CLOSED"
                        ],
                    },
                )
            )

        if error is not None:
            self._denied += 1
            await self._event_bus.publish(
                Event(
                    event_type="tool.invocation.denied",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload={
                        "invocation_id": invocation_id,
                        "tool_id": tool_snapshot["id"],
                        "tool_key": tool_snapshot["tool_key"],
                        "reasons": deny_reasons,
                    },
                )
            )
            raise ToolPermissionDenied(error)

        tool_proxy = type("ToolSnapshot", (), tool_snapshot)()
        handler = self._handlers.get(tool_snapshot["handler_ref"])
        tool_metadata = deepcopy(tool_snapshot["metadata_json"] or {})
        raw_bindings = tool_metadata.pop("secret_bindings", {})
        if raw_bindings is None:
            raw_bindings = {}
        if not isinstance(raw_bindings, dict):
            raise ToolExecutionError("tool.metadata.secret_bindings must be an object.")
        secret_bindings = {
            str(alias).strip(): str(reference).strip()
            for alias, reference in raw_bindings.items()
            if str(alias).strip() and str(reference).strip()
        }
        invalid_refs = [
            alias for alias, reference in secret_bindings.items()
            if not reference.startswith("secret://")
        ]
        if invalid_refs:
            raise ToolExecutionError(
                "Tool secret bindings must use secret:// references: "
                + ", ".join(sorted(invalid_refs))
            )
        secret_accessor: SecretLeaseAccessor | None = None
        if secret_bindings:
            if self._secret_manager is None:
                raise ToolExecutionError(
                    "Tool requires secret bindings but Secret Manager is unavailable."
                )
            secret_accessor = await build_secret_accessor(
                manager=self._secret_manager,
                bindings=secret_bindings,
                context=SecretResolveContext(
                    actor_id=agent_id or "tool-runtime",
                    purpose=f"Execute tool {tool_snapshot['tool_key']}",
                    workspace_id=workspace_id,
                    auth_method="runtime",
                    source="tool_execution_runtime",
                    consumer_type="tool",
                    consumer_key=tool_snapshot["tool_key"],
                    correlation_id=correlation_id,
                    metadata={
                        "invocation_id": invocation_id,
                        "plan_id": plan_id,
                        "step_id": step_id,
                    },
                ),
                ttl_seconds=min(
                    max(5, int(tool_snapshot["timeout_seconds"]) + 5),
                    900,
                ),
            )
            tool_metadata["secret_aliases"] = sorted(secret_bindings)

        execution_context = ToolExecutionContext(
            invocation_id=invocation_id,
            tool_id=tool_snapshot["id"],
            tool_key=tool_snapshot["tool_key"],
            handler_ref=tool_snapshot["handler_ref"],
            workspace_id=workspace_id,
            agent_id=agent_id,
            plan_id=plan_id,
            step_id=step_id,
            correlation_id=correlation_id,
            input=deepcopy(input_data),
            isolation_mode=tool_snapshot["isolation_mode"],
            capabilities={
                "network": tool_snapshot["allow_network"],
                "filesystem_read": tool_snapshot["allow_filesystem_read"],
                "filesystem_write": tool_snapshot["allow_filesystem_write"],
                "secrets": bool(secret_bindings),
            },
            metadata=tool_metadata,
            secret_accessor=secret_accessor,
        )

        self._active.add(invocation_id)
        self._started += 1
        started = perf_counter()

        await self._event_bus.publish(
            Event(
                event_type="tool.invocation.started",
                source="tool_execution_runtime",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload={
                    "invocation_id": invocation_id,
                    "tool_id": tool_snapshot["id"],
                    "tool_key": tool_snapshot["tool_key"],
                    "plan_id": plan_id,
                    "step_id": step_id,
                },
            )
        )

        try:
            output = await self._sandbox.execute(
                tool=tool_proxy,
                handler=handler,
                context=execution_context,
            )
            if secret_accessor is not None:
                output = secret_accessor.redact_object(output)
            duration_ms = (perf_counter() - started) * 1000
            self._finish_invocation(
                invocation_id,
                status="completed",
                output=output,
                error=None,
                duration_ms=duration_ms,
            )
            self._completed += 1

            await self._event_bus.publish(
                Event(
                    event_type="tool.invocation.completed",
                    source="tool_execution_runtime",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    payload={
                        "invocation_id": invocation_id,
                        "tool_id": tool_snapshot["id"],
                        "tool_key": tool_snapshot["tool_key"],
                        "duration_ms": duration_ms,
                    },
                )
            )
            return output, invocation_id
        except TimeoutError as exc:
            duration_ms = (perf_counter() - started) * 1000
            error_message = (
                "Tool execution exceeded timeout of "
                f"{tool_snapshot['timeout_seconds']} seconds."
            )
            self._finish_invocation(
                invocation_id,
                status="timed_out",
                output=None,
                error=error_message,
                duration_ms=duration_ms,
            )
            self._timed_out += 1
            await self._publish_failure(
                "tool.invocation.timed_out",
                invocation_id,
                tool_snapshot,
                workspace_id,
                correlation_id,
                error_message,
                duration_ms,
            )
            raise ToolExecutionError(error_message) from exc
        except asyncio.CancelledError:
            duration_ms = (perf_counter() - started) * 1000
            self._finish_invocation(
                invocation_id,
                status="cancelled",
                output=None,
                error="Tool execution cancelled.",
                duration_ms=duration_ms,
            )
            raise
        except Exception as exc:
            duration_ms = (perf_counter() - started) * 1000
            raw_error = str(exc)
            safe_error = (
                secret_accessor.redact_text(raw_error)
                if secret_accessor is not None
                else raw_error
            )
            self._finish_invocation(
                invocation_id,
                status="failed",
                output=None,
                error=safe_error,
                duration_ms=duration_ms,
            )
            self._failed += 1
            await self._publish_failure(
                "tool.invocation.failed",
                invocation_id,
                tool_snapshot,
                workspace_id,
                correlation_id,
                safe_error,
                duration_ms,
                error_type=exc.__class__.__name__,
            )
            if safe_error != raw_error:
                raise ToolExecutionError(safe_error) from exc
            raise
        finally:
            if secret_accessor is not None:
                await secret_accessor.close()
            self._active.discard(invocation_id)

    def _finish_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = ToolRepository(session)
            row = repository.get_invocation(invocation_id)
            if row is None:
                return
            repository.finish_invocation(
                row,
                status=status,
                output=output,
                error=error,
                duration_ms=duration_ms,
            )

    async def _publish_failure(
        self,
        event_type: str,
        invocation_id: str,
        tool: dict[str, Any],
        workspace_id: str | None,
        correlation_id: str | None,
        message: str,
        duration_ms: float,
        *,
        error_type: str = "TimeoutError",
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="tool_execution_runtime",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload={
                    "invocation_id": invocation_id,
                    "tool_id": tool["id"],
                    "tool_key": tool["tool_key"],
                    "error_type": error_type,
                    "message": message,
                    "duration_ms": duration_ms,
                },
            )
        )

    def _register_defaults(self) -> None:
        self._handlers.register("builtin.echo", self._echo)
        self._handlers.register("builtin.merge", self._merge)
        self._handlers.register("builtin.select", self._select)
        self._handlers.register("builtin.sleep", self._sleep)

    @staticmethod
    async def _echo(context: ToolExecutionContext) -> dict[str, Any]:
        return deepcopy(context.input)

    @staticmethod
    async def _merge(context: ToolExecutionContext) -> dict[str, Any]:
        items = context.input.get("items")
        if not isinstance(items, list):
            raise ToolExecutionError("merge.items должен быть list.")
        output: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ToolExecutionError(
                    "Каждый элемент merge.items должен быть object."
                )
            output.update(item)
        return output

    @classmethod
    async def _select(cls, context: ToolExecutionContext) -> dict[str, Any]:
        path = str(context.input.get("path", "")).strip()
        data = context.input.get("data")
        if not path:
            raise ToolExecutionError("select.path обязателен.")
        current = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    raise ToolExecutionError(
                        f"select.path не найден: {path}."
                    )
                current = current[index]
            else:
                raise ToolExecutionError(
                    f"select.path не найден: {path}."
                )
        return {"value": deepcopy(current)}

    @staticmethod
    async def _sleep(context: ToolExecutionContext) -> dict[str, Any]:
        seconds = float(context.input.get("seconds", 0))
        await asyncio.sleep(seconds)
        return {"slept_seconds": seconds}


class ToolAwareExecutionPlanRuntime(ExecutionPlanRuntime):
    def __init__(
        self,
        *,
        tool_runtime: ToolExecutionRuntime,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_runtime = tool_runtime

    async def _dispatch(
        self,
        context: StepExecutionContext,
    ) -> StepExecutionResult | dict[str, Any]:
        if ExecutionStepType(context.step_type) == ExecutionStepType.TOOL:
            return await self._tool_runtime.execute_step(context)
        return await super()._dispatch(context)
