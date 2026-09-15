from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agent_governance import (
    AGENT_POLICY_ACTION_ID_METADATA_KEY,
    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY,
    AGENT_POLICY_TOOL_ID_METADATA_KEY,
    AgentProfileService,
    AgentToolRuntimeGovernance,
    built_in_agent_profiles,
    fingerprint_agent_tool_input,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.database.models import WorkspaceModel
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.tool_runtime import (
    ToolApprovalConflictError,
    ToolApprovalCredentialError,
    ToolApprovalRequired,
    ToolExecutionRuntime,
    ToolSchemaValidationError,
)
from backend.orchestration.tool_schemas import (
    DirectToolExecutionRequest,
    ToolCreate,
    ToolPermissionCreate,
    ToolUpdate,
)
from backend.orchestration.tools import (
    ToolPermissionDenied,
    ToolRegistryService,
    ToolRepository,
)
from backend.policy_approvals.core import PolicyApprovalStatus
from backend.policy_approvals.service import PolicyApprovalService
from backend.routers import tools as tools_router
from backend.runtime_policy import (
    DataClassification,
    PolicyOperation,
)
from backend.services.workspace_policy import (
    EffectiveWorkspacePolicy,
)


WORKSPACE_ID = "workspace1"


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope


def add_workspace(session) -> None:
    session.add(
        WorkspaceModel(
            id=WORKSPACE_ID,
            name=WORKSPACE_ID,
            description="",
            workspace_type="development",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


def workspace_policy(
    session,
    workspace_id: str,
) -> EffectiveWorkspacePolicy:
    assert workspace_id == WORKSPACE_ID
    return EffectiveWorkspacePolicy(
        workspace_id=workspace_id,
        provider="local",
        model="test",
        temperature=0.2,
        max_tokens=2048,
        memory_mode="workspace",
        network_access="restricted",
        filesystem_access="read_write",
        data_classification=DataClassification.INTERNAL,
        provider_trust={},
        enabled_plugins=[],
        disabled_plugins=[],
    )


def select_profile(session, profile_id: str) -> None:
    profile = next(
        item
        for item in built_in_agent_profiles()
        if item.profile_id == profile_id
    )
    AgentProfileService(session).select_active(
        workspace_id=WORKSPACE_ID,
        profile_id=profile.profile_id,
        version=profile.version,
        expected_fingerprint=profile.fingerprint,
        actor_id="operator1",
    )


def governed_read_create(
    *,
    governed_tool_id: str = "filesystem.read",
    action_id: str = "filesystem.read",
) -> ToolCreate:
    return ToolCreate(
        workspace_id=WORKSPACE_ID,
        tool_key="governed.read",
        display_name="Governed read",
        handler_ref="builtin.echo",
        risk_level="low",
        allow_filesystem_read=True,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        metadata={
            AGENT_POLICY_TOOL_ID_METADATA_KEY: (
                governed_tool_id
            ),
            AGENT_POLICY_ACTION_ID_METADATA_KEY: action_id,
            AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                PolicyOperation.METADATA_VALIDATION.value
            ),
        },
    )


def governed_stage_create() -> ToolCreate:
    return ToolCreate(
        workspace_id=WORKSPACE_ID,
        tool_key="governed.stage",
        display_name="Governed stage",
        handler_ref="builtin.echo",
        kind="builtin",
        risk_level="low",
        allow_filesystem_write=True,
        input_schema={
            "type": "object",
            "required": ["paths"],
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        metadata={
            AGENT_POLICY_TOOL_ID_METADATA_KEY: "git.stage",
            AGENT_POLICY_ACTION_ID_METADATA_KEY: "git.stage",
            AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                PolicyOperation.METADATA_VALIDATION.value
            ),
        },
    )


async def create_governed_stage_tool(
    scope,
    event_bus,
) -> str:
    with scope() as session:
        add_workspace(session)
        select_profile(session, "safe-development")

        service = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        )

        created = await service.create(
            governed_stage_create()
        )

        await service.add_permission(
            created["id"],
            ToolPermissionCreate(
                workspace_id=WORKSPACE_ID,
                effect="allow",
                created_by="operator1",
            ),
        )

        return created["id"]


def runtime(scope, event_bus):
    return ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
        agent_governance=AgentToolRuntimeGovernance(
            workspace_policy_resolver=workspace_policy,
        ),
    )


@pytest.mark.asyncio
async def test_active_profile_allows_exact_governed_read() -> None:
    scope = make_scope()
    event_bus = EventBus()
    events = []

    async def capture(event):
        events.append(event)

    event_bus.subscribe(
        "agent.tool.enforcement.evaluated",
        capture,
    )

    with scope() as session:
        add_workspace(session)
        select_profile(session, "safe-development")
        tool = ToolRepository(session).create(
            governed_read_create()
        )
        tool_id = tool.id

    result = await runtime(
        scope,
        event_bus,
    ).execute_direct(
        tool_id,
        DirectToolExecutionRequest(
            workspace_id=WORKSPACE_ID,
            input={"value": 42},
        ),
    )

    assert result is not None
    assert result["output"] == {"value": 42}

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "completed"
    assert invocation["policy"]["execution_allowed"] is True
    governance = invocation["policy"]["agent_governance"]
    assert governance["profile"]["profile_id"] == "safe-development"
    assert governance["decision"]["action"] == "allow"
    assert governance["input_fingerprint"]
    assert len(events) == 1

    payload = events[0].payload

    assert payload["decision_action"] == "allow"
    assert payload["input_fingerprint"] == (
        fingerprint_agent_tool_input({"value": 42})
    )
    assert payload["trusted_invocation_fingerprint"] is None
    assert payload["external_domain"] is None

    assert set(payload) == {
        "invocation_id",
        "registry_tool_id",
        "governed_tool_id",
        "action_id",
        "profile_id",
        "profile_fingerprint",
        "binding_fingerprint",
        "input_fingerprint",
        "trusted_invocation_fingerprint",
        "external_domain",
        "decision_action",
        "decision_fingerprint",
        "approval_required",
        "isolation_required",
        "reason_codes",
    }

    assert "input" not in payload
    assert "output" not in payload


@pytest.mark.asyncio
async def test_profile_denial_blocks_handler_execution() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        add_workspace(session)
        select_profile(session, "safe-development")
        tool = ToolRepository(session).create(
            governed_read_create(
                governed_tool_id="documents.read",
                action_id="documents.read",
            )
        )
        tool_id = tool.id

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    with pytest.raises(ToolPermissionDenied):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={"value": "sensitive"},
            ),
        )

    assert called is False

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "denied"
    assert invocation["policy"]["execution_allowed"] is False
    assert (
        invocation["policy"]["agent_governance"]
        ["decision"]["action"]
        == "deny"
    )


@pytest.mark.asyncio
async def test_active_profile_rejects_unbound_tool() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        add_workspace(session)
        select_profile(session, "safe-development")
        service = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        )
        service.seed_defaults()
        echo = next(
            item
            for item in service.list()
            if item["tool_key"] == "echo"
        )

    with pytest.raises(ToolPermissionDenied):
        await runtime(
            scope,
            event_bus,
        ).execute_direct(
            echo["id"],
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={"value": 1},
            ),
        )

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "denied"
    assert invocation["policy"]["execution_allowed"] is False
    assert invocation["policy"]["agent_governance"] == {
        "failed_closed": True,
        "error_type": "AgentToolRuntimeGovernanceError",
    }


@pytest.mark.asyncio
async def test_workspace_without_active_profile_keeps_legacy_path() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        add_workspace(session)
        service = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        )
        service.seed_defaults()
        echo = next(
            item
            for item in service.list()
            if item["tool_key"] == "echo"
        )

    result = await runtime(
        scope,
        event_bus,
    ).execute_direct(
        echo["id"],
        DirectToolExecutionRequest(
            workspace_id=WORKSPACE_ID,
            input={"legacy": True},
        ),
    )

    assert result is not None
    assert result["output"] == {"legacy": True}

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "completed"
    assert invocation["policy"]["execution_allowed"] is True
    assert "agent_governance" not in invocation["policy"]


@pytest.mark.asyncio
async def test_network_capability_fails_closed_before_handler() -> None:
    scope = make_scope()
    event_bus = EventBus()
    failed_closed_events = []

    async def capture(event):
        failed_closed_events.append(event)

    event_bus.subscribe(
        "agent.tool.enforcement.failed_closed",
        capture,
    )

    with scope() as session:
        add_workspace(session)
        select_profile(session, "research-readonly")

        service = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        )

        created = await service.create(
            ToolCreate(
                workspace_id=WORKSPACE_ID,
                tool_key="governed.network",
                display_name="Governed network",
                handler_ref="builtin.echo",
                kind="builtin",
                risk_level="low",
                allow_network=True,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                metadata={
                    AGENT_POLICY_TOOL_ID_METADATA_KEY: (
                        "network.fetch"
                    ),
                    AGENT_POLICY_ACTION_ID_METADATA_KEY: (
                        "network.fetch"
                    ),
                    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                        PolicyOperation.METADATA_VALIDATION.value
                    ),
                },
            )
        )

        await service.add_permission(
            created["id"],
            ToolPermissionCreate(
                workspace_id=WORKSPACE_ID,
                effect="allow",
                created_by="operator1",
            ),
        )

        tool_id = created["id"]

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    with pytest.raises(ToolPermissionDenied):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={
                    "url": (
                        "https://github.com/repository/path"
                        "?private-value=must-not-leak"
                    )
                },
            ),
        )

    assert called is False

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "denied"
    assert invocation["policy"]["execution_allowed"] is False
    assert invocation["policy"]["agent_governance"] == {
        "failed_closed": True,
        "error_type": (
            "AgentToolRuntimeBoundaryUnavailableError"
        ),
    }

    assert len(failed_closed_events) == 1

    payload = failed_closed_events[0].payload

    assert payload["invocation_id"] == invocation["id"]
    assert payload["tool_id"] == tool_id
    assert payload["reason_codes"] == [
        "AGENT_GOVERNANCE_FAILED_CLOSED"
    ]

    assert "url" not in payload
    assert "input" not in payload
    assert "private-value" not in payload


@pytest.mark.asyncio
async def test_network_destination_is_checked_by_exact_profile_domain() -> None:
    scope = make_scope()
    event_bus = EventBus()
    failed_closed_events = []
    enforcement_events = []

    async def capture_failed(event):
        failed_closed_events.append(event)

    async def capture_enforcement(event):
        enforcement_events.append(event)

    event_bus.subscribe(
        "agent.tool.enforcement.failed_closed",
        capture_failed,
    )
    event_bus.subscribe(
        "agent.tool.enforcement.evaluated",
        capture_enforcement,
    )

    with scope() as session:
        add_workspace(session)
        select_profile(session, "research-readonly")

        service = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        )

        created = await service.create(
            ToolCreate(
                workspace_id=WORKSPACE_ID,
                tool_key="governed.network.domain-check",
                display_name="Governed network domain check",
                handler_ref="builtin.echo",
                kind="builtin",
                risk_level="low",
                allow_network=True,
                input_schema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1}
                    },
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                metadata={
                    AGENT_POLICY_TOOL_ID_METADATA_KEY: "network.fetch",
                    AGENT_POLICY_ACTION_ID_METADATA_KEY: "network.fetch",
                    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                        PolicyOperation.METADATA_VALIDATION.value
                    ),
                },
            )
        )

        await service.add_permission(
            created["id"],
            ToolPermissionCreate(
                workspace_id=WORKSPACE_ID,
                effect="allow",
                created_by="operator1",
            ),
        )

        tool_id = created["id"]

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    secret_path = "/private/repository/path"
    secret_query = "token=must-not-leak"

    with pytest.raises(ToolPermissionDenied):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={
                    "url": (
                        "https://github.com.evil.example"
                        f"{secret_path}?{secret_query}"
                    )
                },
            ),
        )

    assert called is False
    assert failed_closed_events == []

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    governance = invocation["policy"]["agent_governance"]
    trusted = governance["trusted_invocation"]

    assert invocation["status"] == "denied"
    assert invocation["policy"]["execution_allowed"] is False
    assert governance["decision"]["action"] == "deny"
    assert (
        "PROFILE_DOMAIN_NOT_ALLOWED"
        in governance["decision"]["reason_codes"]
    )
    assert trusted["external_domain"] == "github.com.evil.example"
    assert trusted["action_id"] == "network.fetch"
    assert trusted["governed_tool_id"] == "network.fetch"
    assert trusted["resolver_id"] == "network.fetch.https-url"

    assert len(enforcement_events) == 1
    event_payload = enforcement_events[0].payload
    assert event_payload["external_domain"] == "github.com.evil.example"
    assert event_payload["decision_action"] == "deny"

    policy_text = str(invocation["policy"])
    event_text = str(event_payload)

    assert secret_path not in policy_text
    assert secret_query not in policy_text
    assert secret_path not in event_text
    assert secret_query not in event_text


@pytest.mark.asyncio
async def test_code_execution_fails_closed_without_isolated_runtime() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        add_workspace(session)
        select_profile(session, "safe-development")

        tool = ToolRepository(session).create(
            ToolCreate(
                workspace_id=WORKSPACE_ID,
                tool_key="governed.tests",
                display_name="Governed tests",
                handler_ref="builtin.echo",
                kind="builtin",
                risk_level="low",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                metadata={
                    AGENT_POLICY_TOOL_ID_METADATA_KEY: (
                        "tests.run"
                    ),
                    AGENT_POLICY_ACTION_ID_METADATA_KEY: (
                        "tests.run"
                    ),
                    AGENT_POLICY_RUNTIME_OPERATION_METADATA_KEY: (
                        PolicyOperation.CODE_EXECUTION.value
                    ),
                },
            )
        )

        tool_id = tool.id

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    with pytest.raises(ToolPermissionDenied):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={"command": "pytest"},
            ),
        )

    assert called is False

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]

    assert invocation["status"] == "denied"
    assert invocation["policy"]["execution_allowed"] is False

    governance = invocation["policy"]["agent_governance"]

    assert governance["decision"]["action"] == "deny"
    assert governance["decision"]["approval_required"] is False
    assert governance["decision"]["isolation_required"] is False

    runtime_layers = [
        layer
        for layer in governance["layers"]
        if layer["layer"] == "runtime_policy"
    ]

    assert len(runtime_layers) == 1
    assert runtime_layers[0]["action"] == "deny"
    assert runtime_layers[0]["reason_codes"] == [
        "RUNTIME_BLOCKED"
    ]

@pytest.mark.asyncio
async def test_direct_approval_request_is_persisted_without_invocation() -> None:
    scope = make_scope()
    event_bus = EventBus()
    approval_events = []

    async def capture(event):
        approval_events.append(event)

    event_bus.subscribe(
        "agent.tool.approval.required",
        capture,
    )

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    input_data = {
        "paths": ["backend/example.py"],
    }

    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    error = caught.value

    assert called is False
    assert error.execution_id.startswith(
        "agent_tool_exec_"
    )
    assert error.approval_id.startswith(
        "policy_approval_"
    )
    assert len(error.scope_fingerprint) == 64
    assert error.created is True

    assert "PROFILE_APPROVAL_REPOSITORY_STAGE" in (
        error.reason_codes
    )
    assert "ENFORCEMENT_APPROVAL_REQUIRED" in (
        error.reason_codes
    )

    with scope() as session:
        approval_records = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).list(
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert invocations == []
    assert len(approval_records) == 1

    record = approval_records[0]

    assert record.id == error.approval_id
    assert record.status == PolicyApprovalStatus.PENDING
    assert (
        record.scope.subject_type
        == "agent_tool_execution"
    )
    assert (
        record.scope.subject_id
        == error.execution_id
    )
    assert (
        record.scope_fingerprint
        == error.scope_fingerprint
    )
    assert (
        record.requested_by
        == "tool_execution_runtime"
    )

    payload = record.scope.subject_payload

    assert payload["governed_tool_id"] == "git.stage"
    assert payload["action_id"] == "git.stage"
    assert payload["input_fingerprint"] == (
        fingerprint_agent_tool_input(input_data)
    )

    assert len(approval_events) == 1

    event_payload = approval_events[0].payload

    assert (
        event_payload["approval_id"]
        == error.approval_id
    )
    assert (
        event_payload["execution_id"]
        == error.execution_id
    )
    assert (
        event_payload["scope_fingerprint"]
        == error.scope_fingerprint
    )
    assert event_payload["input_fingerprint"] == (
        fingerprint_agent_tool_input(input_data)
    )

    assert set(event_payload) == {
        "schema_version",
        "code",
        "execution_id",
        "approval_id",
        "scope_fingerprint",
        "expires_at",
        "reason_codes",
        "created",
        "registry_tool_id",
        "governed_tool_id",
        "action_id",
        "profile_id",
        "profile_fingerprint",
        "binding_fingerprint",
        "input_fingerprint",
        "decision_fingerprint",
    }

    assert "input" not in event_payload
    assert "output" not in event_payload
    assert tool_runtime.stats()["denied"] == 0


@pytest.mark.asyncio
async def test_invalid_input_cannot_receive_agent_tool_approval() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )

    called = False
    tool_runtime = runtime(scope, event_bus)

    async def should_not_run(context):
        nonlocal called
        called = True
        return {"unexpected": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        should_not_run,
        replace=True,
    )

    with pytest.raises(ToolSchemaValidationError):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={"unexpected": True},
            ),
        )

    assert called is False

    with scope() as session:
        approvals = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).list(
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approvals == []
    assert invocations == []


def test_tools_router_returns_428_for_typed_approval_precondition() -> None:
    class ApprovalRuntime:
        async def execute_direct(
            self,
            tool_id,
            request,
        ):
            raise ToolApprovalRequired(
                execution_id="agent_tool_exec_test",
                approval_id="policy_approval_test",
                scope_fingerprint="a" * 64,
                expires_at=(
                    "2026-09-11T12:00:00+00:00"
                ),
                reason_codes=(
                    "PROFILE_APPROVAL_REPOSITORY_STAGE",
                    "ENFORCEMENT_APPROVAL_REQUIRED",
                ),
                created=True,
            )

    app = FastAPI()
    app.include_router(
        tools_router.router,
        prefix="/api",
    )

    app.dependency_overrides[
        tools_router.get_tool_runtime
    ] = lambda: ApprovalRuntime()

    with TestClient(app) as client:
        response = client.post(
            "/api/tools/tool_test/execute",
            json={
                "workspace_id": WORKSPACE_ID,
                "input": {
                    "paths": ["backend/example.py"],
                },
            },
        )

    assert response.status_code == 428

    detail = response.json()["detail"]

    assert detail == {
        "schema_version": (
            "p3-002.2b-c2.1.direct-approval-request"
        ),
        "code": "AGENT_TOOL_APPROVAL_REQUIRED",
        "execution_id": "agent_tool_exec_test",
        "approval_id": "policy_approval_test",
        "scope_fingerprint": "a" * 64,
        "expires_at": (
            "2026-09-11T12:00:00+00:00"
        ),
        "reason_codes": [
            "PROFILE_APPROVAL_REPOSITORY_STAGE",
            "ENFORCEMENT_APPROVAL_REQUIRED",
        ],
        "created": True,
    }

async def approve_agent_tool_request(
    *,
    scope,
    event_bus,
    approval_id: str,
) -> str:
    with scope() as session:
        grant = await PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).approve(
            approval_id=approval_id,
            workspace_id=WORKSPACE_ID,
            decided_by="operator1",
        )
        return grant.token


@pytest.mark.asyncio
async def test_consumed_exact_approval_executes_once_with_human_control() -> None:
    scope = make_scope()
    event_bus = EventBus()
    consumed_events = []

    async def capture(event):
        consumed_events.append(event)

    event_bus.subscribe(
        "agent.tool.approval.consumed",
        capture,
    )

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)

    calls = 0

    async def handler(context):
        nonlocal calls
        calls += 1
        return {"staged": list(context.input["paths"])}

    tool_runtime.handlers.register(
        "builtin.echo",
        handler,
        replace=True,
    )

    input_data = {"paths": ["backend/example.py"]}

    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    required = caught.value

    token = await approve_agent_tool_request(
        scope=scope,
        event_bus=event_bus,
        approval_id=required.approval_id,
    )

    request = DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input=input_data,
        approval_execution_id=required.execution_id,
        approval_id=required.approval_id,
        approval_token=token,
    )

    assert token not in repr(request)

    result = await tool_runtime.execute_direct(
        tool_id,
        request,
    )

    assert result is not None
    assert result["output"] == {
        "staged": ["backend/example.py"],
    }
    assert calls == 1

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.CONSUMED
    assert len(invocations) == 1

    invocation = invocations[0]

    assert invocation["status"] == "completed"
    assert invocation["policy"]["execution_allowed"] is True

    governance = invocation["policy"]["agent_governance"]

    assert governance["decision"]["action"] == "allow"
    assert governance["decision"]["approval_required"] is False
    assert governance["decision"]["isolation_required"] is False

    human_layers = [
        layer
        for layer in governance["layers"]
        if layer["layer"] == "human_control"
    ]

    assert len(human_layers) == 1
    assert human_layers[0]["action"] == "allow"
    assert human_layers[0]["reason_codes"] == [
        "EXACT_SCOPE_APPROVAL_CONSUMED"
    ]

    approval_policy = invocation["policy"]["agent_approval"]

    assert approval_policy["execution_id"] == required.execution_id
    assert (
        approval_policy["evidence"]["approval_id"]
        == required.approval_id
    )
    assert (
        approval_policy["evidence"]["input_fingerprint"]
        == fingerprint_agent_tool_input(input_data)
    )

    assert token not in str(invocation)
    assert len(consumed_events) == 1
    assert token not in str(consumed_events[0].payload)

    consumed_payload = consumed_events[0].payload
    assert consumed_payload["approval_id"] == required.approval_id
    assert (
        consumed_payload["execution_id"]
        == required.execution_id
    )


@pytest.mark.asyncio
async def test_consumed_approval_replay_is_rejected_without_second_invocation() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)

    calls = 0

    async def handler(context):
        nonlocal calls
        calls += 1
        return {"ok": True}

    tool_runtime.handlers.register(
        "builtin.echo",
        handler,
        replace=True,
    )

    input_data = {"paths": ["backend/example.py"]}

    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    required = caught.value
    token = await approve_agent_tool_request(
        scope=scope,
        event_bus=event_bus,
        approval_id=required.approval_id,
    )

    approved_request = DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input=input_data,
        approval_execution_id=required.execution_id,
        approval_id=required.approval_id,
        approval_token=token,
    )

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_request,
    )

    assert result is not None
    assert calls == 1

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            approved_request,
        )

    assert calls == 1

    with scope() as session:
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert len(invocations) == 1
    assert invocations[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_changed_input_does_not_consume_exact_approval_token() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)

    input_data = {"paths": ["backend/original.py"]}

    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    required = caught.value
    token = await approve_agent_tool_request(
        scope=scope,
        event_bus=event_bus,
        approval_id=required.approval_id,
    )

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input={"paths": ["backend/changed.py"]},
                approval_execution_id=required.execution_id,
                approval_id=required.approval_id,
                approval_token=token,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    result = await tool_runtime.execute_direct(
        tool_id,
        DirectToolExecutionRequest(
            workspace_id=WORKSPACE_ID,
            input=input_data,
            approval_execution_id=required.execution_id,
            approval_id=required.approval_id,
            approval_token=token,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_changed_profile_does_not_consume_exact_approval_token() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)

    input_data = {"paths": ["backend/profile.py"]}

    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    required = caught.value
    token = await approve_agent_tool_request(
        scope=scope,
        event_bus=event_bus,
        approval_id=required.approval_id,
    )

    with scope() as session:
        select_profile(session, "release-manager")

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
                approval_execution_id=required.execution_id,
                approval_id=required.approval_id,
                approval_token=token,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    with scope() as session:
        select_profile(session, "safe-development")

    result = await tool_runtime.execute_direct(
        tool_id,
        DirectToolExecutionRequest(
            workspace_id=WORKSPACE_ID,
            input=input_data,
            approval_execution_id=required.execution_id,
            approval_id=required.approval_id,
            approval_token=token,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_partial_approval_bundle_fails_before_tool_resolution() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)

    request = DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input={"paths": ["backend/example.py"]},
        approval_id="policy_approval_test",
    )

    with pytest.raises(ToolApprovalCredentialError):
        await tool_runtime.execute_direct(
            tool_id,
            request,
        )

    with scope() as session:
        approvals = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).list(
            workspace_id=WORKSPACE_ID,
        )

        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approvals == []
    assert invocations == []


@pytest.mark.parametrize(
    ("runtime_error", "expected_status"),
    [
        (
            ToolApprovalCredentialError(
                "Human Control approval token is invalid."
            ),
            403,
        ),
        (
            ToolApprovalConflictError(
                "Human Control approval is stale."
            ),
            409,
        ),
    ],
)
def test_tools_router_maps_approval_resubmit_errors(
    runtime_error,
    expected_status,
) -> None:
    class FailingRuntime:
        async def execute_direct(
            self,
            tool_id,
            request,
        ):
            raise runtime_error

    app = FastAPI()
    app.include_router(
        tools_router.router,
        prefix="/api",
    )
    app.dependency_overrides[
        tools_router.get_tool_runtime
    ] = lambda: FailingRuntime()

    with TestClient(app) as client:
        response = client.post(
            "/api/tools/tool_test/execute",
            json={
                "workspace_id": WORKSPACE_ID,
                "input": {
                    "paths": ["backend/example.py"],
                },
            },
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == str(runtime_error)

def runtime_with_policy_state(
    scope,
    event_bus,
    state,
):
    def resolver(
        session,
        workspace_id: str,
    ) -> EffectiveWorkspacePolicy:
        assert workspace_id == WORKSPACE_ID
        return EffectiveWorkspacePolicy(
            workspace_id=workspace_id,
            provider="local",
            model="test",
            temperature=0.2,
            max_tokens=2048,
            memory_mode="workspace",
            network_access=state.get(
                "network_access",
                "restricted",
            ),
            filesystem_access=state.get(
                "filesystem_access",
                "read_write",
            ),
            data_classification=state.get(
                "data_classification",
                DataClassification.INTERNAL,
            ),
            provider_trust={},
            enabled_plugins=[],
            disabled_plugins=[],
        )

    return ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
        agent_governance=AgentToolRuntimeGovernance(
            workspace_policy_resolver=resolver,
        ),
    )


async def request_and_approve_stage(
    *,
    scope,
    event_bus,
    tool_runtime,
    tool_id: str,
    input_data: dict,
):
    with pytest.raises(ToolApprovalRequired) as caught:
        await tool_runtime.execute_direct(
            tool_id,
            DirectToolExecutionRequest(
                workspace_id=WORKSPACE_ID,
                input=input_data,
            ),
        )

    required = caught.value
    token = await approve_agent_tool_request(
        scope=scope,
        event_bus=event_bus,
        approval_id=required.approval_id,
    )
    return required, token


def approved_resubmit_request(
    *,
    required,
    token: str,
    input_data: dict,
) -> DirectToolExecutionRequest:
    return DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input=input_data,
        approval_execution_id=required.execution_id,
        approval_id=required.approval_id,
        approval_token=token,
    )


@pytest.mark.asyncio
async def test_changed_tool_binding_does_not_consume_exact_approval_token() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)
    input_data = {"paths": ["backend/binding.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    with scope() as session:
        repository = ToolRepository(session)
        tool = repository.get(tool_id)
        assert tool is not None
        repository.update(
            tool,
            ToolUpdate(risk_level="medium"),
        )

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            approved_resubmit_request(
                required=required,
                token=token,
                input_data=input_data,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    with scope() as session:
        repository = ToolRepository(session)
        tool = repository.get(tool_id)
        assert tool is not None
        repository.update(
            tool,
            ToolUpdate(risk_level="low"),
        )

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_workspace_and_runtime_policy_drift_does_not_consume_token() -> None:
    scope = make_scope()
    event_bus = EventBus()
    state = {
        "filesystem_access": "read_write",
        "network_access": "restricted",
        "data_classification": DataClassification.INTERNAL,
    }

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime_with_policy_state(
        scope,
        event_bus,
        state,
    )
    input_data = {"paths": ["backend/policy-drift.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    # PUBLIC remains permitted by safe-development and git.stage still
    # requires approval, but Workspace/Runtime evidence fingerprints move.
    state["data_classification"] = DataClassification.PUBLIC

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            approved_resubmit_request(
                required=required,
                token=token,
                input_data=input_data,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    state["data_classification"] = DataClassification.INTERNAL

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_canonical_deny_wins_without_consuming_approval_token() -> None:
    scope = make_scope()
    event_bus = EventBus()
    state = {
        "filesystem_access": "read_write",
        "network_access": "restricted",
        "data_classification": DataClassification.INTERNAL,
    }

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime_with_policy_state(
        scope,
        event_bus,
        state,
    )
    input_data = {"paths": ["backend/deny-first.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    state["filesystem_access"] = "read_only"

    with pytest.raises(ToolPermissionDenied):
        await tool_runtime.execute_direct(
            tool_id,
            approved_resubmit_request(
                required=required,
                token=token,
                input_data=input_data,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert len(invocations) == 1
    assert invocations[0]["status"] == "denied"
    assert (
        invocations[0]["policy"]["agent_governance"]
        ["decision"]["action"]
        == "deny"
    )

    state["filesystem_access"] = "read_write"

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )

    assert approval.status == PolicyApprovalStatus.CONSUMED


@pytest.mark.asyncio
async def test_invalid_token_does_not_create_invocation_or_consume_approval() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)
    input_data = {"paths": ["backend/token.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    wrong_token = (
        ("0" if token[0] != "0" else "1")
        + token[1:]
    )
    assert wrong_token != token

    with pytest.raises(ToolApprovalCredentialError):
        await tool_runtime.execute_direct(
            tool_id,
            approved_resubmit_request(
                required=required,
                token=wrong_token,
                input_data=input_data,
            ),
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_wrong_approval_id_does_not_consume_real_approval() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)
    input_data = {"paths": ["backend/approval-id.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    wrong_request = DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input=input_data,
        approval_execution_id=required.execution_id,
        approval_id="policy_approval_missing",
        approval_token=token,
    )

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            wrong_request,
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()

    assert approval.status == PolicyApprovalStatus.APPROVED
    assert invocations == []

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_wrong_execution_id_does_not_consume_exact_approval() -> None:
    scope = make_scope()
    event_bus = EventBus()

    tool_id = await create_governed_stage_tool(
        scope,
        event_bus,
    )
    tool_runtime = runtime(scope, event_bus)
    input_data = {"paths": ["backend/execution-id.py"]}

    required, token = await request_and_approve_stage(
        scope=scope,
        event_bus=event_bus,
        tool_runtime=tool_runtime,
        tool_id=tool_id,
        input_data=input_data,
    )

    wrong_request = DirectToolExecutionRequest(
        workspace_id=WORKSPACE_ID,
        input=input_data,
        approval_execution_id="agent_tool_exec_wrong",
        approval_id=required.approval_id,
        approval_token=token,
    )

    with pytest.raises(ToolApprovalConflictError):
        await tool_runtime.execute_direct(
            tool_id,
            wrong_request,
        )

    with scope() as session:
        approval = PolicyApprovalService(
            session=session,
            event_bus=event_bus,
        ).get(
            approval_id=required.approval_id,
            workspace_id=WORKSPACE_ID,
        )

    assert approval.status == PolicyApprovalStatus.APPROVED

    result = await tool_runtime.execute_direct(
        tool_id,
        approved_resubmit_request(
            required=required,
            token=token,
            input_data=input_data,
        ),
    )

    assert result is not None
