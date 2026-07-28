from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.container import AppContainer
from backend.core.events import EventBus
from backend.core.config import AppSettings
from backend.database.base import Base
from backend.main import app


def make_settings() -> AppSettings:
    return AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )


def test_container_builds_workspace_services() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    container = AppContainer(
        settings=make_settings(),
        event_bus=EventBus(),
        plugin_loader=app.state.container.plugin_loader,
    )

    with Session(engine) as session:
        assert container.workspace_service(session) is not None
        assert container.workspace_policy_service(session) is not None
        assert container.active_workspace_service(session) is not None


def test_container_diagnostics_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/system/container")

    assert response.status_code == 200

    payload = response.json()
    assert payload["initialized"] is True
    assert payload["dependencies"]["settings"] == "AppSettings"
    assert payload["dependencies"]["event_bus"] == "EventBus"
