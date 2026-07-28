from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.container import AppContainer
from backend.core.events import Event
from backend.core.logging import LoggerManager
from backend.database.init_db import initialize_database
from backend.database.session import session_scope
from backend.middleware.request_logging import RequestLoggingMiddleware
from backend.control_center.security import HumanControlAuthMiddleware
from backend.middleware.workspace_context import WorkspaceContextMiddleware
from backend.routers import (
    agent_collaboration,
    autonomous_missions,
    agents,
    chat,
    code_sandbox,
    council,
    container,
    context,
    events,
    execution_distribution,
    execution_observability,
    execution_planner,
    execution_plans,
    execution_reviews,
    execution_runtime,
    execution_supervisor,
    execution_transport,
    gateway,
    health,
    human_control,
    human_control_auth,
    human_control_backup,
    human_control_browser,
    human_control_compliance,
    human_control_governance,
    human_control_notifications,
    human_control_retention,
    human_control_routing,
    memory,
    mission_forecasting,
    mission_governance,
    mission_learning,
    mission_memory,
    mission_portfolio,
    mission_resources,
    mission_schedules,
    mission_strategies,
    models,
    plugins,
    repository,
    secrets,
    settings,
    system,
    task_approvals,
    task_audit,
    task_budgets,
    task_dead_letters,
    task_executor,
    task_scheduler,
    task_workflows,
    tasks,
    tools,
    workflow_templates,
    workspace_policy,
    workspace_resources,
    workspace_state,
    workspaces,
)

app_container = AppContainer.build_default()
app_settings = app_container.settings

LoggerManager.configure(debug=app_settings.debug)
logger = LoggerManager.get_logger("system")


async def log_system_event(event: Event) -> None:
    logger.info(
        "System event id=%s type=%s source=%s",
        event.id,
        event.event_type,
        event.source,
    )


app_container.event_bus.subscribe("system.*", log_system_event)


async def persist_task_audit_event(event: Event) -> None:
    manager = app_container.task_audit_manager

    if manager is None:
        return

    try:
        await manager.record_event(event)
    except Exception:
        logger.exception(
            "Failed to persist task audit event id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe("task.*", persist_task_audit_event)
app_container.event_bus.subscribe("workflow.*", persist_task_audit_event)
app_container.event_bus.subscribe(
    "execution_plan.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "agent.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_context.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "tool.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_observability.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_worker.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_dispatch.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_lease.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_distribution.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "execution_transport.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "mission.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "autonomy.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "human_control.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "secret.*",
    persist_task_audit_event,
)
app_container.event_bus.subscribe(
    "code_sandbox.*",
    persist_task_audit_event,
)


async def persist_human_control_operator_event(event: Event) -> None:
    service = app_container.human_control_compliance_service
    if service is None:
        return
    try:
        await service.record_event(event)
    except Exception:
        logger.exception(
            "Failed to persist Human Control operator audit event id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "human_control.*",
    persist_human_control_operator_event,
)
app_container.event_bus.subscribe(
    "secret.*",
    persist_human_control_operator_event,
)


async def mirror_execution_event(event: Event) -> None:
    transport = app_container.execution_event_transport
    if transport is None:
        return
    try:
        await transport.mirror_event(event)
    except Exception:
        logger.exception(
            "Failed to mirror execution event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _transport_event_pattern in (
    "execution_plan.runtime.*",
    "execution_plan.step.*",
    "execution_worker.*",
    "execution_dispatch.*",
    "execution_lease.*",
    "tool.invocation.*",
    "agent.delegation.*",
    "mission.*",
    "autonomy.*",
    "human_control.*",
    "secret.*",
):
    app_container.event_bus.subscribe(
        _transport_event_pattern,
        mirror_execution_event,
    )


async def settle_task_cost_event(event: Event) -> None:
    manager = app_container.task_admission_manager
    if manager is None:
        return

    try:
        await manager.handle_execution_event(event)
    except Exception:
        logger.exception(
            "Failed to settle Task cost event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _cost_event_type in (
    "task.executor.completed",
    "task.executor.failed",
    "task.executor.timed_out",
    "task.cancelled",
):
    app_container.event_bus.subscribe(
        _cost_event_type,
        settle_task_cost_event,
    )



async def handle_dead_letter_event(event: Event) -> None:
    manager = app_container.task_dead_letter_manager
    if manager is None:
        return

    try:
        await manager.capture_from_event(event)
        await manager.handle_replay_terminal_event(event)
    except Exception:
        logger.exception(
            "Failed to process Dead Letter Queue event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _dead_letter_event_type in (
    "task.executor.completed",
    "task.executor.failed",
    "task.executor.timed_out",
    "task.cancelled",
    "task.skipped",
):
    app_container.event_bus.subscribe(
        _dead_letter_event_type,
        handle_dead_letter_event,
    )


async def handle_execution_plan_failure(event: Event) -> None:
    planner = app_container.execution_planner_service
    if planner is None:
        return

    try:
        await planner.handle_runtime_failure(event)
    except Exception:
        logger.exception(
            "Failed to process automatic replanning "
            "event id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "execution_plan.runtime.failed",
    handle_execution_plan_failure,
)


async def handle_autonomous_mission_event(event: Event) -> None:
    service = app_container.autonomous_mission_service
    if service is None:
        return
    try:
        await service.handle_execution_event(event)
    except Exception:
        logger.exception(
            "Failed to process autonomous Mission event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_execution_event_type in (
    "execution_plan.runtime.completed",
    "execution_plan.runtime.failed",
    "execution_plan.runtime.cancelled",
):
    app_container.event_bus.subscribe(
        _mission_execution_event_type,
        handle_autonomous_mission_event,
    )


async def handle_mission_memory_event(event: Event) -> None:
    service = app_container.mission_memory_service
    if service is None:
        return
    try:
        await service.handle_execution_event(event)
    except Exception:
        logger.exception(
            "Failed to capture Mission evidence from event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "execution_plan.runtime.completed",
    handle_mission_memory_event,
)


async def handle_mission_governance_event(event: Event) -> None:
    service = app_container.mission_governance_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Governance event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "mission.cycle.failed",
    handle_mission_governance_event,
)


async def handle_mission_strategy_event(event: Event) -> None:
    service = app_container.mission_strategy_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Strategy event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_strategy_event_type in (
    "mission.cycle.plan_created",
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
):
    app_container.event_bus.subscribe(
        _mission_strategy_event_type,
        handle_mission_strategy_event,
    )


async def handle_mission_resource_event(event: Event) -> None:
    service = app_container.mission_resource_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Resource event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_resource_event_type in (
    "mission.cycle.plan_created",
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
):
    app_container.event_bus.subscribe(
        _mission_resource_event_type,
        handle_mission_resource_event,
    )


async def handle_workspace_resource_event(event: Event) -> None:
    service = app_container.workspace_resource_coordinator
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Workspace Resource event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _workspace_resource_event_type in (
    "mission.resource.usage.recorded",
    "mission.cycle.plan_created",
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
):
    app_container.event_bus.subscribe(
        _workspace_resource_event_type,
        handle_workspace_resource_event,
    )


async def handle_mission_portfolio_event(event: Event) -> None:
    service = app_container.mission_portfolio_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Portfolio event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_portfolio_event_type in (
    "mission.activated",
    "mission.paused",
    "mission.completed",
    "mission.failed",
    "mission.cancelled",
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
):
    app_container.event_bus.subscribe(
        _mission_portfolio_event_type,
        handle_mission_portfolio_event,
    )


async def handle_mission_forecast_event(event: Event) -> None:
    service = app_container.mission_forecast_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Forecast event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_forecast_event_type in (
    "mission.progress.recorded",
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
    "mission.risk.created",
    "mission.risk.updated",
    "mission.risk.assessed",
    "mission.strategy.selected",
    "mission.resource.usage.recorded",
    "mission.dependency.status_changed",
):
    app_container.event_bus.subscribe(
        _mission_forecast_event_type,
        handle_mission_forecast_event,
    )




async def handle_mission_learning_event(event: Event) -> None:
    service = app_container.mission_learning_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Mission Learning event id=%s type=%s",
            event.id,
            event.event_type,
        )


for _mission_learning_event_type in (
    "mission.cycle.completed",
    "mission.cycle.failed",
    "mission.cycle.cancelled",
    "mission.completed",
    "mission.failed",
    "mission.cancelled",
):
    app_container.event_bus.subscribe(
        _mission_learning_event_type,
        handle_mission_learning_event,
    )


async def handle_execution_supervisor_event(event: Event) -> None:
    supervisor = app_container.execution_supervisor_service
    if supervisor is None:
        return

    try:
        await supervisor.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Execution Supervisor event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


for _supervisor_event_pattern in (
    "execution_plan.runtime.*",
    "execution_plan.step.*",
    "execution_plan.planner.replanned",
):
    app_container.event_bus.subscribe(
        _supervisor_event_pattern,
        handle_execution_supervisor_event,
    )


async def handle_execution_observability_event(event: Event) -> None:
    service = app_container.execution_observability_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process execution observability event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


for _observability_event_pattern in (
    "execution_plan.*",
    "agent.*",
    "tool.*",
    "execution_worker.*",
    "execution_dispatch.*",
    "execution_lease.*",
):
    app_container.event_bus.subscribe(
        _observability_event_pattern,
        handle_execution_observability_event,
    )


async def handle_human_control_source_event(event: Event) -> None:
    service = app_container.human_control_center_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to reconcile Human Control Center event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


for _human_control_source_pattern in (
    "task.approval.*",
    "mission.checkpoint.*",
    "mission.resource.allocation.*",
    "mission.workspace_resource.reservation.*",
    "mission.learning.*",
):
    app_container.event_bus.subscribe(
        _human_control_source_pattern,
        handle_human_control_source_event,
    )


async def handle_human_control_notification_event(event: Event) -> None:
    service = app_container.human_control_notification_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Human Control notification event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "human_control.*",
    handle_human_control_notification_event,
)


async def handle_human_control_routing_event(event: Event) -> None:
    service = app_container.human_control_routing_service
    if service is None:
        return
    try:
        await service.handle_event(event)
    except Exception:
        logger.exception(
            "Failed to process Human Control routing event "
            "id=%s type=%s",
            event.id,
            event.event_type,
        )


app_container.event_bus.subscribe(
    "human_control.notification.*",
    handle_human_control_routing_event,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.container = app_container

    initialize_database()

    with session_scope() as session:
        app_container.model_manager(session).seed_defaults()
        seeded_agents = (
            app_container.agent_registry_service(session).seed_defaults()
        )
        if seeded_agents:
            logger.info("Seeded default agents count=%s", seeded_agents)
        seeded_tools = (
            app_container.tool_registry_service(session).seed_defaults()
        )
        if seeded_tools:
            logger.info("Seeded default tools count=%s", seeded_tools)

    await app_container.plugin_loader.discover()
    await app_container.plugin_loader.load_all()
    app_container.plugin_loader.register_routers(app)

    await app_container.start_task_runtime()

    logger.info(
        "Application started name=%s version=%s environment=%s plugins=%s",
        app_settings.app_name,
        app_settings.app_version,
        app_settings.environment,
        len(app_container.plugin_loader.list_plugins()),
    )

    await app_container.event_bus.publish(
        Event(
            event_type="system.started",
            source="backend",
            payload={
                "app_name": app_settings.app_name,
                "app_version": app_settings.app_version,
            },
        )
    )

    try:
        yield
    finally:
        council_live_manager = getattr(
            app.state,
            "council_live_manager",
            None,
        )
        if council_live_manager is not None:
            await council_live_manager.shutdown()

        await app_container.stop_task_runtime()

        await app_container.event_bus.publish(
            Event(
                event_type="system.stopped",
                source="backend",
            )
        )
        logger.info("Application shutdown completed")


app = FastAPI(
    title=app_settings.app_name,
    version=app_settings.app_version,
    description="Backend API for AI Studio Enterprise.",
    lifespan=lifespan,
)

app.state.container = app_container

app.add_middleware(HumanControlAuthMiddleware)
app.add_middleware(WorkspaceContextMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Workspace-ID", "X-Workspace-Source", "X-Authenticated-Actor", "X-Authentication-Method"],
)

app.include_router(health.router, prefix="/api")
app.include_router(human_control.router, prefix="/api")
app.include_router(human_control_auth.router, prefix="/api")
app.include_router(human_control_backup.router, prefix="/api")
app.include_router(human_control_browser.router, prefix="/api")
app.include_router(human_control_compliance.router, prefix="/api")
app.include_router(human_control_governance.router, prefix="/api")
app.include_router(human_control_notifications.router, prefix="/api")
app.include_router(human_control_retention.router, prefix="/api")
app.include_router(human_control_routing.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(agent_collaboration.router, prefix="/api")
app.include_router(autonomous_missions.router, prefix="/api")
app.include_router(mission_forecasting.router, prefix="/api")
app.include_router(mission_governance.router, prefix="/api")
app.include_router(mission_learning.router, prefix="/api")
app.include_router(mission_memory.router, prefix="/api")
app.include_router(mission_portfolio.router, prefix="/api")
app.include_router(mission_resources.router, prefix="/api")
app.include_router(mission_schedules.router, prefix="/api")
app.include_router(mission_strategies.router, prefix="/api")
app.include_router(workspace_resources.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(container.router, prefix="/api")
app.include_router(context.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(code_sandbox.router, prefix="/api")
app.include_router(council.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(execution_distribution.router, prefix="/api")
app.include_router(execution_observability.router, prefix="/api")
app.include_router(execution_planner.router, prefix="/api")
app.include_router(execution_reviews.router, prefix="/api")
app.include_router(execution_plans.router, prefix="/api")
app.include_router(execution_runtime.router, prefix="/api")
app.include_router(execution_supervisor.router, prefix="/api")
app.include_router(execution_transport.router, prefix="/api")
app.include_router(gateway.router, prefix="/api")
app.include_router(repository.router, prefix="/api")
app.include_router(plugins.router, prefix="/api")
app.include_router(secrets.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(task_scheduler.router, prefix="/api")
app.include_router(task_approvals.router, prefix="/api")
app.include_router(task_audit.router, prefix="/api")
app.include_router(task_budgets.router, prefix="/api")
app.include_router(task_dead_letters.router, prefix="/api")
app.include_router(task_executor.router, prefix="/api")
app.include_router(task_workflows.router, prefix="/api")
app.include_router(workflow_templates.router, prefix="/api")

app.include_router(workspace_state.router, prefix="/api")
app.include_router(workspace_policy.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "name": app_settings.app_name,
        "version": app_settings.app_version,
        "environment": app_settings.environment,
        "status": "running",
    }
