from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from threading import Event as ThreadEvent
from typing import Literal, Protocol
import uuid

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.council.history import CouncilHistoryService
from backend.council.repository import CouncilRunRepository
from backend.council.schemas import (
    CouncilLiveCancelResponse,
    CouncilLiveStartResponse,
    CouncilLiveStatusResponse,
    CouncilMemberResult,
    CouncilRunRequest,
    CouncilRunResponse,
)
from backend.council.service import (
    CouncilCancelledError,
    CouncilExecutionError,
    CouncilService,
)
from backend.database.session import session_scope
from backend.gateway.service import AIGateway
from backend.repositories.settings import SettingsRepository


LiveRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]
LiveRunKind = Literal["run", "replay", "retry_failed"]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.failed", "run.cancelled"}
)


class CouncilHistoryWriter(Protocol):
    async def record_success(
        self,
        *,
        request: CouncilRunRequest,
        result: CouncilRunResponse,
        replay_of_run_id: str | None,
    ) -> None: ...

    async def record_failure(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilExecutionError,
        replay_of_run_id: str | None,
    ) -> None: ...

    async def record_cancelled(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilCancelledError,
        replay_of_run_id: str | None,
    ) -> None: ...


class DatabaseCouncilHistoryWriter:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    def _service(self, session) -> CouncilHistoryService:
        return CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=self._event_bus,
            settings_repository=SettingsRepository(session),
        )

    async def record_success(
        self,
        *,
        request: CouncilRunRequest,
        result: CouncilRunResponse,
        replay_of_run_id: str | None,
    ) -> None:
        with session_scope() as session:
            await self._service(session).record_success(
                request=request,
                result=result,
                replay_of_run_id=replay_of_run_id,
            )

    async def record_failure(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilExecutionError,
        replay_of_run_id: str | None,
    ) -> None:
        with session_scope() as session:
            await self._service(session).record_failure(
                request=request,
                error=error,
                replay_of_run_id=replay_of_run_id,
            )

    async def record_cancelled(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilCancelledError,
        replay_of_run_id: str | None,
    ) -> None:
        with session_scope() as session:
            await self._service(session).record_cancelled(
                request=request,
                error=error,
                replay_of_run_id=replay_of_run_id,
            )


@dataclass(frozen=True)
class CouncilLiveEvent:
    sequence: int
    event_type: str
    data: dict
    created_at: datetime

    def to_sse(self) -> str:
        payload = {
            **self.data,
            "event_type": self.event_type,
            "sequence": self.sequence,
            "created_at": self.created_at.isoformat(),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            f"id: {self.sequence}\n"
            f"event: {self.event_type}\n"
            f"data: {encoded}\n\n"
        )


@dataclass
class CouncilLiveRun:
    run_id: str
    request: CouncilRunRequest
    kind: LiveRunKind
    replay_of_run_id: str | None
    reused_member_results: dict[int, CouncilMemberResult]
    status: LiveRunStatus = "queued"
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime | None = None
    events: deque[CouncilLiveEvent] = field(
        default_factory=lambda: deque(maxlen=4096)
    )
    sequence: int = 0
    condition: asyncio.Condition = field(
        default_factory=asyncio.Condition
    )
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    provider_cancel_event: ThreadEvent = field(default_factory=ThreadEvent)
    task: asyncio.Task[None] | None = None


class CouncilLiveManager:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        history_writer: CouncilHistoryWriter | None = None,
        completed_ttl_seconds: int = 600,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        self._event_bus = event_bus
        self._history_writer = (
            history_writer or DatabaseCouncilHistoryWriter(event_bus)
        )
        self._completed_ttl = timedelta(
            seconds=max(60, completed_ttl_seconds)
        )
        self._heartbeat_seconds = max(1.0, heartbeat_seconds)
        self._runs: dict[str, CouncilLiveRun] = {}
        self._lock = asyncio.Lock()
        self._logger = LoggerManager.get_logger("council_live")

    async def start(
        self,
        *,
        request: CouncilRunRequest,
        gateway: AIGateway,
        kind: LiveRunKind = "run",
        replay_of_run_id: str | None = None,
        reused_member_results: dict[int, CouncilMemberResult] | None = None,
    ) -> CouncilLiveStartResponse:
        await self._prune()
        run_id = f"council_{uuid.uuid4().hex}"
        state = CouncilLiveRun(
            run_id=run_id,
            request=request,
            kind=kind,
            replay_of_run_id=replay_of_run_id,
            reused_member_results=dict(reused_member_results or {}),
        )
        async with self._lock:
            self._runs[run_id] = state

        await self._publish(
            state,
            "run.accepted",
            {
                "run_id": run_id,
                "kind": kind,
                "replay_of_run_id": replay_of_run_id,
            },
        )
        state.task = asyncio.create_task(
            self._execute(state, gateway),
            name=f"{run_id}:live",
        )
        return CouncilLiveStartResponse(
            run_id=run_id,
            status=state.status,
            kind=kind,
            replay_of_run_id=replay_of_run_id,
            events_url=f"/api/council/live/{run_id}/events",
            status_url=f"/api/council/live/{run_id}",
            cancel_url=f"/api/council/live/{run_id}",
        )

    async def status(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilLiveStatusResponse | None:
        state = await self._state_for(run_id, workspace_id)
        if state is None:
            return None
        return CouncilLiveStatusResponse(
            run_id=state.run_id,
            workspace_id=state.request.workspace_id,
            status=state.status,
            kind=state.kind,
            replay_of_run_id=state.replay_of_run_id,
            event_count=state.sequence,
            started_at=state.started_at,
            finished_at=state.finished_at,
        )

    async def cancel(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilLiveCancelResponse | None:
        state = await self._state_for(run_id, workspace_id)
        if state is None:
            return None

        requested = False
        if state.status not in TERMINAL_STATUSES:
            requested = not state.cancel_event.is_set()
            state.cancel_event.set()
            state.provider_cancel_event.set()
            if requested:
                await self._publish(
                    state,
                    "run.cancel.requested",
                    {"run_id": run_id},
                )
        return CouncilLiveCancelResponse(
            run_id=run_id,
            cancellation_requested=requested,
            status=state.status,
        )

    async def stream(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
        last_event_id: int = 0,
    ) -> AsyncIterator[str]:
        state = await self._state_for(run_id, workspace_id)
        if state is None:
            return

        cursor = max(0, last_event_id)
        while True:
            heartbeat = False
            pending: list[CouncilLiveEvent] = []
            terminal_event_published = False

            async with state.condition:
                pending = [
                    item
                    for item in state.events
                    if item.sequence > cursor
                ]
                terminal_event_published = any(
                    item.event_type in TERMINAL_EVENT_TYPES
                    for item in state.events
                )
                if not pending and not terminal_event_published:
                    try:
                        await asyncio.wait_for(
                            state.condition.wait(),
                            timeout=self._heartbeat_seconds,
                        )
                    except TimeoutError:
                        heartbeat = True
                    continue_after_wait = not heartbeat
                else:
                    continue_after_wait = False

            if continue_after_wait:
                continue
            if heartbeat:
                yield ": keep-alive\n\n"
                continue

            for item in pending:
                cursor = item.sequence
                yield item.to_sse()

            if terminal_event_published and not any(
                item.sequence > cursor for item in state.events
            ):
                return

    async def shutdown(self) -> None:
        async with self._lock:
            states = list(self._runs.values())
        active = [
            state
            for state in states
            if state.task is not None and not state.task.done()
        ]
        for state in active:
            state.cancel_event.set()
            state.provider_cancel_event.set()
        if not active:
            return
        tasks = [state.task for state in active if state.task is not None]
        done, pending = await asyncio.wait(tasks, timeout=5.0)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                task.exception()

    async def _execute(
        self,
        state: CouncilLiveRun,
        gateway: AIGateway,
    ) -> None:
        state.status = "running"
        service = CouncilService(
            gateway=gateway,
            event_bus=self._event_bus,
        )

        async def progress(event_type: str, data: dict) -> None:
            await self._publish(state, event_type, data)

        try:
            result = await service.run(
                state.request,
                run_id=state.run_id,
                progress=progress,
                cancel_event=state.cancel_event,
                cancellation_check=state.provider_cancel_event.is_set,
                reused_member_results=state.reused_member_results,
            )
        except CouncilCancelledError as exc:
            history_saved = await self._persist_cancelled(state, exc)
            state.status = "cancelled"
            state.finished_at = exc.finished_at
            await self._publish(
                state,
                "run.cancelled",
                {
                    "detail": {
                        "code": "COUNCIL_CANCELLED",
                        "message": str(exc),
                        "run_id": exc.run_id,
                        "history_saved": history_saved,
                        "members": [
                            member.model_dump(mode="json")
                            for member in exc.member_results
                        ],
                    }
                },
            )
        except CouncilExecutionError as exc:
            history_saved = await self._persist_failure(state, exc)
            state.status = "failed"
            state.finished_at = exc.finished_at
            await self._publish(
                state,
                "run.failed",
                {
                    "detail": {
                        "code": "COUNCIL_ALL_MEMBERS_FAILED",
                        "message": str(exc),
                        "run_id": exc.run_id,
                        "history_saved": history_saved,
                        "members": [
                            member.model_dump(mode="json")
                            for member in exc.member_results
                        ],
                    }
                },
            )
        except asyncio.CancelledError:
            state.status = "cancelled"
            state.finished_at = datetime.now(timezone.utc)
            await self._publish(
                state,
                "run.cancelled",
                {
                    "detail": {
                        "code": "COUNCIL_SHUTDOWN",
                        "message": "Live-запуск остановлен при завершении.",
                        "run_id": state.run_id,
                        "history_saved": False,
                        "members": [],
                    }
                },
            )
        except Exception as exc:
            self._logger.exception(
                "Council live run failed run_id=%s",
                state.run_id,
            )
            state.status = "failed"
            state.finished_at = datetime.now(timezone.utc)
            await self._publish(
                state,
                "run.failed",
                {
                    "detail": {
                        "code": "COUNCIL_LIVE_INTERNAL_ERROR",
                        "message": str(exc),
                        "run_id": state.run_id,
                        "history_saved": False,
                        "members": [],
                    }
                },
            )
        else:
            history_saved = await self._persist_success(state, result)
            state.status = "completed"
            state.finished_at = result.finished_at
            delivered = result.model_copy(
                update={
                    "history_saved": history_saved,
                    "replay_of_run_id": state.replay_of_run_id,
                }
            )
            await self._publish(
                state,
                "run.completed",
                {"result": delivered.model_dump(mode="json")},
            )

    async def _persist_success(
        self,
        state: CouncilLiveRun,
        result: CouncilRunResponse,
    ) -> bool:
        try:
            await self._history_writer.record_success(
                request=state.request,
                result=result,
                replay_of_run_id=state.replay_of_run_id,
            )
            return True
        except Exception:
            await self._persistence_failed(state)
            return False

    async def _persist_failure(
        self,
        state: CouncilLiveRun,
        error: CouncilExecutionError,
    ) -> bool:
        try:
            await self._history_writer.record_failure(
                request=state.request,
                error=error,
                replay_of_run_id=state.replay_of_run_id,
            )
            return True
        except Exception:
            await self._persistence_failed(state)
            return False

    async def _persist_cancelled(
        self,
        state: CouncilLiveRun,
        error: CouncilCancelledError,
    ) -> bool:
        try:
            await self._history_writer.record_cancelled(
                request=state.request,
                error=error,
                replay_of_run_id=state.replay_of_run_id,
            )
            return True
        except Exception:
            await self._persistence_failed(state)
            return False

    async def _persistence_failed(self, state: CouncilLiveRun) -> None:
        self._logger.exception(
            "Council live history persistence failed run_id=%s",
            state.run_id,
        )
        await self._event_bus.publish(
            Event(
                event_type="council.live.persistence.failed",
                source="council_live",
                workspace_id=state.request.workspace_id,
                payload={"run_id": state.run_id},
            )
        )
        await self._publish(
            state,
            "history.failed",
            {"run_id": state.run_id},
        )

    async def _publish(
        self,
        state: CouncilLiveRun,
        event_type: str,
        data: dict,
    ) -> None:
        async with state.condition:
            state.sequence += 1
            state.events.append(
                CouncilLiveEvent(
                    sequence=state.sequence,
                    event_type=event_type,
                    data=data,
                    created_at=datetime.now(timezone.utc),
                )
            )
            state.condition.notify_all()

    async def _state_for(
        self,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilLiveRun | None:
        async with self._lock:
            state = self._runs.get(run_id)
        if state is None or state.request.workspace_id != workspace_id:
            return None
        return state

    async def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._completed_ttl
        async with self._lock:
            expired = [
                run_id
                for run_id, state in self._runs.items()
                if state.finished_at is not None
                and state.finished_at < cutoff
            ]
            for run_id in expired:
                self._runs.pop(run_id, None)
