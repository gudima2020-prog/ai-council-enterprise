from __future__ import annotations

from contextlib import contextmanager

import pytest
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
    ToolExecutionRuntime,
)
from backend.orchestration.tool_schemas import (
    DirectToolExecutionRequest,
    ToolCreate,
    ToolPermissionCreate,
)
from backend.orchestration.tools import (
    ToolPermissionDenied,
    ToolRegistryService,
    ToolRepository,
)
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

    assert set(payload) == {
        "invocation_id",
        "registry_tool_id",
        "governed_tool_id",
        "action_id",
        "profile_id",
        "profile_fingerprint",
        "binding_fingerprint",
        "input_fingerprint",
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
                    "url": "https://example.invalid/private-value"
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
