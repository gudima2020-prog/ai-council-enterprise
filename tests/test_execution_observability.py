from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.models import ExecutionPlanModel
from backend.orchestration.observability import (
    ExecutionObservabilityService,
    percentile,
)
from backend.orchestration.observability_schemas import (
    ExecutionSLOPolicyUpdate,
)
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

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


def add_plan(scope, *, status: str, duration_ms: float) -> str:
    finished = datetime.now(timezone.utc)
    started = finished - timedelta(milliseconds=duration_ms)
    with scope() as session:
        row = ExecutionPlanModel(
            title=f"Plan {status}",
            objective="Observability test.",
            status=status,
            started_at=started,
            finished_at=finished,
            created_at=started,
            updated_at=finished,
        )
        session.add(row)
        session.flush()
        return row.id


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([], 95) is None
    assert percentile([100], 95) == 100
    assert percentile([10, 20, 30, 40, 50], 95) == 50
    assert percentile([10, 20, 30, 40, 50], 50) == 30


def test_dashboard_reports_plan_success_and_latency() -> None:
    scope = make_scope()
    add_plan(scope, status="completed", duration_ms=1000)
    add_plan(scope, status="failed", duration_ms=3000)

    service = ExecutionObservabilityService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    dashboard = service.dashboard(window_minutes=60)

    assert dashboard["plans"]["total"] == 2
    assert dashboard["plans"]["terminal_sample_size"] == 2
    assert dashboard["plans"]["success_rate_pct"] == 50.0
    assert dashboard["plans"]["p50_duration_ms"] == 1000.0
    assert dashboard["plans"]["p95_duration_ms"] == 3000.0
    assert dashboard["health"] == "degraded"


@pytest.mark.asyncio
async def test_slo_breach_is_created_and_auto_resolved() -> None:
    scope = make_scope()
    add_plan(scope, status="failed", duration_ms=1000)
    service = ExecutionObservabilityService(
        event_bus=EventBus(),
        session_factory=scope,
    )
    service._ensure_default_policy()
    policy = service.get_effective_policy(None)
    assert policy is not None

    service.update_policy(
        policy["id"],
        ExecutionSLOPolicyUpdate(
            min_sample_size=1,
            target_plan_success_rate_pct=90,
        ),
    )
    failed_evaluation = await service.evaluate_slos(
        policy_id=policy["id"],
        persist_snapshot=True,
    )
    assert failed_evaluation["evaluated"] == 1
    checks = failed_evaluation["results"][0]["checks"]
    success_check = next(
        row for row in checks if row["metric_name"] == "plan_success_rate_pct"
    )
    assert success_check["status"] == "breached"
    breaches = service.list_breaches(status="open")
    assert len(breaches) == 1

    service.update_policy(
        policy["id"],
        ExecutionSLOPolicyUpdate(target_plan_success_rate_pct=0),
    )
    recovered = await service.evaluate_slos(
        policy_id=policy["id"],
        persist_snapshot=False,
    )
    recovered_check = next(
        row
        for row in recovered["results"][0]["checks"]
        if row["metric_name"] == "plan_success_rate_pct"
    )
    assert recovered_check["status"] == "met"
    assert service.list_breaches(status="open") == []
    assert len(service.list_breaches(status="resolved")) == 1
    assert len(service.list_snapshots()) == 1


def test_prometheus_export_contains_core_metrics() -> None:
    scope = make_scope()
    add_plan(scope, status="completed", duration_ms=500)
    service = ExecutionObservabilityService(
        event_bus=EventBus(),
        session_factory=scope,
    )

    text = service.prometheus_metrics(window_minutes=60)

    assert "ai_studio_execution_plans_total" in text
    assert "ai_studio_execution_plan_success_rate_percent" in text
    assert 'scope="global"' in text
