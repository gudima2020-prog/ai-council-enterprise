from __future__ import annotations

import pytest

from backend.orchestration.planner import (
    BuiltinPlannerAdapter,
    PlannerAdapterNotRegistered,
    PlannerAdapterRegistry,
)


def test_planner_adapter_registry_is_normalized() -> None:
    registry = PlannerAdapterRegistry()
    adapter = BuiltinPlannerAdapter()

    registry.register("Custom.Planner", adapter)

    assert registry.get(" custom.planner ") is adapter
    assert registry.diagnostics() == ["custom.planner"]


def test_planner_adapter_registry_rejects_unknown_adapter() -> None:
    registry = PlannerAdapterRegistry()

    with pytest.raises(PlannerAdapterNotRegistered):
        registry.get("missing.planner")
