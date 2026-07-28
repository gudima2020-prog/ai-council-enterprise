from __future__ import annotations

import pytest

from backend.core.config import AppSettings
from backend.core.container import AppContainer
from backend.core.events import EventBus
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


@pytest.mark.asyncio
async def test_task_runtime_is_created_and_discarded() -> None:
    container = AppContainer(
        settings=make_settings(),
        event_bus=EventBus(),
        plugin_loader=app.state.container.plugin_loader,
    )

    assert container.task_queue is None
    assert container.task_scheduler is None

    scheduler = await container.start_task_runtime()

    assert scheduler.running is True
    assert container.task_queue is not None

    await container.stop_task_runtime()

    assert container.task_queue is None
    assert container.task_scheduler is None
