from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import GatewayProviderStatsModel
from backend.database.session import session_scope

SessionScopeFactory = Callable[[], AbstractContextManager[Session]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderHealthService:
    """Passive provider health, reliability and circuit-breaker state.

    State is persisted so routing quality survives application restarts. The
    circuit breaker opens only after consecutive failover-eligible failures.
    """

    def __init__(
        self,
        *,
        session_factory: SessionScopeFactory = session_scope,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(5, int(cooldown_seconds))
        self._lock = RLock()

    def _row(self, session: Session, provider: str) -> GatewayProviderStatsModel:
        row = session.scalar(
            select(GatewayProviderStatsModel).where(
                GatewayProviderStatsModel.provider == provider
            )
        )
        if row is None:
            row = GatewayProviderStatsModel(provider=provider)
            session.add(row)
            session.flush()
        return row

    def circuit_available(self, provider: str) -> bool:
        with self._lock, self._session_factory() as session:
            row = self._row(session, provider)
            if row.circuit_open_until is None:
                return True
            now = utc_now()
            open_until = row.circuit_open_until
            if open_until.tzinfo is None:
                open_until = open_until.replace(tzinfo=timezone.utc)
            if open_until <= now:
                row.circuit_open_until = None
                row.consecutive_failures = 0
                row.updated_at = now
                return True
            return False

    def record_success(self, provider: str, latency_ms: float | None) -> None:
        now = utc_now()
        with self._lock, self._session_factory() as session:
            row = self._row(session, provider)
            row.total_requests += 1
            row.successful_requests += 1
            row.consecutive_failures = 0
            row.last_success_at = now
            row.last_error_code = None
            row.circuit_open_until = None
            if latency_ms is not None:
                value = float(latency_ms)
                row.ema_latency_ms = (
                    value
                    if row.ema_latency_ms is None
                    else round(row.ema_latency_ms * 0.8 + value * 0.2, 3)
                )
            row.updated_at = now

    def record_failure(
        self,
        provider: str,
        latency_ms: float | None,
        error_code: str | None,
        *,
        circuit_eligible: bool,
    ) -> None:
        now = utc_now()
        with self._lock, self._session_factory() as session:
            row = self._row(session, provider)
            row.total_requests += 1
            row.failed_requests += 1
            row.last_failure_at = now
            row.last_error_code = error_code
            if latency_ms is not None:
                value = float(latency_ms)
                row.ema_latency_ms = (
                    value
                    if row.ema_latency_ms is None
                    else round(row.ema_latency_ms * 0.8 + value * 0.2, 3)
                )
            if circuit_eligible:
                row.consecutive_failures += 1
                if row.consecutive_failures >= self.failure_threshold:
                    row.circuit_open_until = now + timedelta(
                        seconds=self.cooldown_seconds
                    )
            else:
                row.consecutive_failures = 0
            row.updated_at = now

    def snapshot(self, provider: str) -> dict:
        with self._lock, self._session_factory() as session:
            row = self._row(session, provider)
            return self._serialize(row)

    def list_snapshots(self) -> list[dict]:
        with self._lock, self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(GatewayProviderStatsModel).order_by(
                        GatewayProviderStatsModel.provider
                    )
                ).all()
            )
            return [self._serialize(row) for row in rows]

    @staticmethod
    def _serialize(row: GatewayProviderStatsModel) -> dict:
        now = utc_now()
        open_until = row.circuit_open_until
        if open_until is not None and open_until.tzinfo is None:
            open_until = open_until.replace(tzinfo=timezone.utc)
        circuit_open = bool(open_until and open_until > now)
        if circuit_open:
            status = "circuit_open"
        elif row.consecutive_failures:
            status = "degraded"
        elif row.successful_requests:
            status = "healthy"
        else:
            status = "unknown"
        reliability = (
            row.successful_requests / row.total_requests
            if row.total_requests
            else None
        )
        return {
            "provider": row.provider,
            "status": status,
            "circuit_open": circuit_open,
            "circuit_open_until": (
                row.circuit_open_until.isoformat()
                if row.circuit_open_until
                else None
            ),
            "total_requests": row.total_requests,
            "successful_requests": row.successful_requests,
            "failed_requests": row.failed_requests,
            "consecutive_failures": row.consecutive_failures,
            "reliability": reliability,
            "ema_latency_ms": row.ema_latency_ms,
            "last_success_at": (
                row.last_success_at.isoformat() if row.last_success_at else None
            ),
            "last_failure_at": (
                row.last_failure_at.isoformat() if row.last_failure_at else None
            ),
            "last_error_code": row.last_error_code,
        }


provider_health = ProviderHealthService()


class TransientProviderHealthService:
    """Process-local health implementation for isolated services/tests."""

    def __init__(self, *, failure_threshold: int = 3) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self._items: dict[str, dict] = {}

    def _item(self, provider: str) -> dict:
        return self._items.setdefault(
            provider,
            {
                "provider": provider,
                "status": "unknown",
                "circuit_open": False,
                "circuit_open_until": None,
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "consecutive_failures": 0,
                "reliability": None,
                "ema_latency_ms": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error_code": None,
            },
        )

    def circuit_available(self, provider: str) -> bool:
        return not bool(self._item(provider)["circuit_open"])

    def record_success(self, provider: str, latency_ms: float | None) -> None:
        item = self._item(provider)
        item["total_requests"] += 1
        item["successful_requests"] += 1
        item["consecutive_failures"] = 0
        item["circuit_open"] = False
        item["status"] = "healthy"
        item["last_error_code"] = None
        if latency_ms is not None:
            item["ema_latency_ms"] = float(latency_ms)
        item["reliability"] = item["successful_requests"] / item["total_requests"]

    def record_failure(
        self,
        provider: str,
        latency_ms: float | None,
        error_code: str | None,
        *,
        circuit_eligible: bool,
    ) -> None:
        item = self._item(provider)
        item["total_requests"] += 1
        item["failed_requests"] += 1
        item["last_error_code"] = error_code
        if latency_ms is not None:
            item["ema_latency_ms"] = float(latency_ms)
        if circuit_eligible:
            item["consecutive_failures"] += 1
            item["circuit_open"] = (
                item["consecutive_failures"] >= self.failure_threshold
            )
        else:
            item["consecutive_failures"] = 0
        item["status"] = "circuit_open" if item["circuit_open"] else "degraded"
        item["reliability"] = item["successful_requests"] / item["total_requests"]

    def snapshot(self, provider: str) -> dict:
        return dict(self._item(provider))

    def list_snapshots(self) -> list[dict]:
        return [dict(self._items[key]) for key in sorted(self._items)]
