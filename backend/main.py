from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import get_settings
from backend.core.events import Event, event_bus
from backend.core.logging import LoggerManager
from backend.database.init_db import initialize_database
from backend.database.session import session_scope
from backend.middleware.request_logging import RequestLoggingMiddleware
from backend.middleware.workspace_context import WorkspaceContextMiddleware
from backend.plugins.registry import plugin_loader
from backend.repositories.models import ModelRepository
from backend.services.model_manager import ModelManager
from backend.routers import (
    chat,
    context,
    events,
    gateway,
    health,
    memory,
    models,
    plugins,
    repository,
    settings,
    system,
    workspace_policy,
    workspace_state,
    workspaces,
)

app_settings = get_settings()
LoggerManager.configure(debug=app_settings.debug)
logger = LoggerManager.get_logger("system")


async def log_system_event(event: Event) -> None:
    logger.info(
        "System event id=%s type=%s source=%s",
        event.id,
        event.event_type,
        event.source,
    )


event_bus.subscribe("system.*", log_system_event)

app = FastAPI(
    title=app_settings.app_name,
    version=app_settings.app_version,
    description="Backend API for AI Studio Enterprise.",
)

app.add_middleware(WorkspaceContextMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Workspace-ID", "X-Workspace-Source"],
)

app.include_router(health.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(context.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(gateway.router, prefix="/api")
app.include_router(repository.router, prefix="/api")
app.include_router(plugins.router, prefix="/api")
app.include_router(memory.router, prefix="/api")

app.include_router(workspace_state.router, prefix="/api")
app.include_router(workspace_policy.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")


@app.on_event("startup")
async def on_startup() -> None:
    initialize_database()

    with session_scope() as session:
        ModelManager(
            ModelRepository(session),
            event_bus,
            app_settings,
        ).seed_defaults()

    await plugin_loader.discover()
    await plugin_loader.load_all()
    plugin_loader.register_routers(app)

    logger.info(
        "Application started name=%s version=%s environment=%s plugins=%s",
        app_settings.app_name,
        app_settings.app_version,
        app_settings.environment,
        len(plugin_loader.list_plugins()),
    )

    await event_bus.publish(
        Event(
            event_type="system.started",
            source="backend",
            payload={
                "app_name": app_settings.app_name,
                "app_version": app_settings.app_version,
            },
        )
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await event_bus.publish(
        Event(
            event_type="system.stopped",
            source="backend",
        )
    )
    logger.info("Application shutdown completed")


@app.get("/")
def root() -> dict:
    return {
        "name": app_settings.app_name,
        "version": app_settings.app_version,
        "environment": app_settings.environment,
        "status": "running",
    }
