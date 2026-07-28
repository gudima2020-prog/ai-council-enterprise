from __future__ import annotations

import pytest

from backend.core.events import EventBus
from backend.orchestration.runtime import ExecutionPlanRuntime


@pytest.mark.asyncio
async def test_runtime_accepts_preflight_hook() -> None:
    runtime = ExecutionPlanRuntime(event_bus=EventBus())
    called = []

    async def preflight(plan_id: str):
        called.append(plan_id)
        return {"decision": "pass"}

    runtime.set_preflight_hook(preflight)

    assert runtime.stats()["preflight_enabled"] is True
    assert runtime._preflight_hook is preflight
    assert called == []
