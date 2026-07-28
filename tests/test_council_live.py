from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.dependencies import (
    get_council_control_service,
    get_council_live_manager,
)
from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.council.live import CouncilLiveManager
from backend.council.schemas import (
    CouncilMemberRequest,
    CouncilMemberResult,
    CouncilRole,
    CouncilRunRequest,
)
from backend.council.service import CouncilExecutionError, CouncilService
from backend.gateway.providers.base import (
    ProviderAdapter,
    ProviderStreamCancelled,
)
from backend.gateway.providers.openrouter import OpenRouterAdapter
from backend.gateway.schemas import (
    GatewayMessage,
    GatewayRequest,
    GatewayResponse,
    GatewayUsage,
)
from backend.gateway.service import AIGateway
from backend.routers import council as council_router


class MemoryHistoryWriter:
    def __init__(self) -> None:
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []
        self.cancellations: list[tuple] = []

    async def record_success(self, **kwargs) -> None:
        self.successes.append((kwargs["request"], kwargs["result"]))

    async def record_failure(self, **kwargs) -> None:
        self.failures.append((kwargs["request"], kwargs["error"]))

    async def record_cancelled(self, **kwargs) -> None:
        self.cancellations.append((kwargs["request"], kwargs["error"]))


class AllowAllCouncilControl:
    async def authorize(self, request, workspace_id):
        return (
            request.model_copy(
                update={
                    "workspace_id": workspace_id,
                    "estimated_cost_usd": 0.0,
                    "cost_estimate_status": "known",
                }
            ),
            None,
        )


class LiveProvider(ProviderAdapter):
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append((request.model, request.source))
        content = self._content(request)
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            status="success",
            usage=GatewayUsage(total_tokens=12),
            latency_ms=2.0,
        )

    def complete_stream(
        self,
        request: GatewayRequest,
        on_delta,
        is_cancelled=None,
    ) -> GatewayResponse:
        self.requests.append((request.model, request.source))
        content = self._content(request)
        midpoint = max(1, len(content) // 2)
        for part in (content[:midpoint], content[midpoint:]):
            if is_cancelled is not None and is_cancelled():
                raise ProviderStreamCancelled("cancelled")
            if part:
                on_delta(part)
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            status="success",
            usage=GatewayUsage(total_tokens=12),
            latency_ms=2.0,
        )

    @staticmethod
    def _content(request: GatewayRequest) -> str:
        if request.source.startswith("council_synthesis"):
            return json.dumps(
                {
                    "final_answer": "Потоковый итог Совета.",
                    "consensus": ["Основной вывод подтверждён."],
                    "disagreements": [],
                    "recommendations": ["Продолжить проверку."],
                    "confidence": 86,
                },
                ensure_ascii=False,
            )
        return f"Ответ модели {request.model}."


class BlockingProvider(ProviderAdapter):
    name = "fake"

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        time.sleep(1)
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content="late",
            status="success",
        )

    def complete_stream(
        self,
        request: GatewayRequest,
        on_delta,
        is_cancelled=None,
    ) -> GatewayResponse:
        for _ in range(500):
            if is_cancelled is not None and is_cancelled():
                raise ProviderStreamCancelled("cancelled")
            time.sleep(0.005)
        return self.complete(request)


class SlowProvider(ProviderAdapter):
    name = "fake"

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        time.sleep(0.15)
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content="too late",
            status="success",
        )


def _settings() -> AppSettings:
    return AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="fake",
        default_model="model-a",
        max_tokens=1000,
        temperature=0.2,
        request_timeout_seconds=60,
    )


def _gateway(provider: ProviderAdapter, event_bus: EventBus) -> AIGateway:
    return AIGateway(
        settings=_settings(),
        event_bus=event_bus,
        providers={"fake": provider},
    )


def _request(workspace_id: str | None = "workspace_one") -> CouncilRunRequest:
    return CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="fake",
                model="model-a",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="fake",
                model="model-b",
                role=CouncilRole.CRITIC,
            ),
        ],
        workspace_id=workspace_id,
        member_timeout_seconds=30,
    )


def _sse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in "".join(chunks).split("\n\n"):
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event_type is not None and data is not None:
            events.append((event_type, data))
    return events


@pytest.mark.asyncio
async def test_live_manager_streams_progress_and_structured_result() -> None:
    event_bus = EventBus()
    history = MemoryHistoryWriter()
    manager = CouncilLiveManager(
        event_bus=event_bus,
        history_writer=history,
        heartbeat_seconds=1,
    )
    started = await manager.start(
        request=_request(),
        gateway=_gateway(LiveProvider(), event_bus),
    )

    chunks = [
        item
        async for item in manager.stream(
            run_id=started.run_id,
            workspace_id="workspace_one",
        )
    ]
    events = _sse_events(chunks)
    event_types = [event_type for event_type, _ in events]
    completed = next(
        data for event_type, data in events
        if event_type == "run.completed"
    )

    assert event_types[0] == "run.accepted"
    assert event_types.count("member.started") == 2
    assert event_types.count("member.completed") == 2
    assert event_types.count("synthesis.delta") >= 2
    assert event_types[-1] == "run.completed"
    assert completed["result"]["history_saved"] is True
    assert completed["result"]["synthesis"]["confidence"] == 86
    assert len(history.successes) == 1

    reconnect_cursor = events[-3][1]["sequence"]
    replayed_chunks = [
        item
        async for item in manager.stream(
            run_id=started.run_id,
            workspace_id="workspace_one",
            last_event_id=reconnect_cursor,
        )
    ]
    replayed = _sse_events(replayed_chunks)
    assert replayed
    assert all(
        data["sequence"] > reconnect_cursor
        for _, data in replayed
    )
    assert replayed[-1][0] == "run.completed"

    status = await manager.status(
        run_id=started.run_id,
        workspace_id="workspace_one",
    )
    hidden = await manager.status(
        run_id=started.run_id,
        workspace_id="workspace_two",
    )
    assert status is not None
    assert status.status == "completed"
    assert hidden is None


@pytest.mark.asyncio
async def test_live_stream_waits_for_terminal_event_after_status_change() -> None:
    class DelayedTerminalManager(CouncilLiveManager):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.before_terminal = asyncio.Event()
            self.release_terminal = asyncio.Event()

        async def _publish(self, state, event_type: str, data: dict) -> None:
            if event_type == "run.completed":
                self.before_terminal.set()
                await self.release_terminal.wait()
            await super()._publish(state, event_type, data)

    event_bus = EventBus()
    manager = DelayedTerminalManager(
        event_bus=event_bus,
        history_writer=MemoryHistoryWriter(),
        heartbeat_seconds=1,
    )
    started = await manager.start(
        request=_request(),
        gateway=_gateway(LiveProvider(), event_bus),
    )

    async def collect() -> list[str]:
        return [
            item
            async for item in manager.stream(
                run_id=started.run_id,
                workspace_id="workspace_one",
            )
        ]

    stream_task = asyncio.create_task(collect())
    await asyncio.wait_for(manager.before_terminal.wait(), timeout=2)
    await asyncio.sleep(0)
    assert stream_task.done() is False

    manager.release_terminal.set()
    chunks = await asyncio.wait_for(stream_task, timeout=2)
    assert _sse_events(chunks)[-1][0] == "run.completed"


@pytest.mark.asyncio
async def test_live_manager_cancels_and_persists_cancelled_run() -> None:
    event_bus = EventBus()
    history = MemoryHistoryWriter()
    manager = CouncilLiveManager(
        event_bus=event_bus,
        history_writer=history,
    )
    started = await manager.start(
        request=_request(),
        gateway=_gateway(BlockingProvider(), event_bus),
    )
    await asyncio.sleep(0.03)

    hidden = await manager.cancel(
        run_id=started.run_id,
        workspace_id="workspace_two",
    )
    cancelled = await manager.cancel(
        run_id=started.run_id,
        workspace_id="workspace_one",
    )
    chunks = [
        item
        async for item in manager.stream(
            run_id=started.run_id,
            workspace_id="workspace_one",
        )
    ]
    events = _sse_events(chunks)

    assert hidden is None
    assert cancelled is not None
    assert cancelled.cancellation_requested is True
    assert events[-1][0] == "run.cancelled"
    assert events[-1][1]["detail"]["history_saved"] is True
    assert len(history.cancellations) == 1
    assert all(
        member.error_code == "COUNCIL_MEMBER_CANCELLED"
        for member in history.cancellations[0][1].member_results
    )


@pytest.mark.asyncio
async def test_member_timeout_returns_explicit_error_code() -> None:
    event_bus = EventBus()
    service = CouncilService(
        gateway=_gateway(SlowProvider(), event_bus),
        event_bus=event_bus,
    )
    request = _request(None).model_copy(
        update={"member_timeout_seconds": 0.02}
    )

    with pytest.raises(CouncilExecutionError) as captured:
        await service.run(request)

    assert len(captured.value.member_results) == 2
    assert {
        member.error_code for member in captured.value.member_results
    } == {"COUNCIL_MEMBER_TIMEOUT"}


@pytest.mark.asyncio
async def test_retry_reuses_success_and_calls_only_failed_member() -> None:
    event_bus = EventBus()
    provider = LiveProvider()
    service = CouncilService(
        gateway=_gateway(provider, event_bus),
        event_bus=event_bus,
    )
    reused = CouncilMemberResult(
        provider="fake",
        model="model-a",
        requested_model="model-a",
        role=CouncilRole.ANALYST,
        label="Аналитик",
        status="success",
        answer="Ранее сохранённый ответ.",
    )
    progress_events: list[str] = []

    async def progress(event_type: str, _data: dict) -> None:
        progress_events.append(event_type)

    result = await service.run(
        _request(None),
        progress=progress,
        reused_member_results={0: reused},
    )

    assert result.status == "completed"
    assert result.members[0].answer == "Ранее сохранённый ответ."
    assert "member.reused" in progress_events
    assert ("model-a", "council_member") not in provider.requests
    assert ("model-b", "council_member") in provider.requests
    assert ("model-a", "council_synthesis") in provider.requests


def test_live_api_starts_streams_and_reports_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = EventBus()
    gateway = _gateway(LiveProvider(), event_bus)
    manager = CouncilLiveManager(
        event_bus=event_bus,
        history_writer=MemoryHistoryWriter(),
    )
    api = FastAPI()
    api.state.container = SimpleNamespace(
        settings=_settings(),
        event_bus=event_bus,
        secret_manager_service=None,
    )
    api.include_router(council_router.router, prefix="/api")
    api.dependency_overrides[get_council_live_manager] = lambda: manager
    api.dependency_overrides[get_council_control_service] = (
        lambda: AllowAllCouncilControl()
    )
    monkeypatch.setattr(
        council_router,
        "build_ai_gateway",
        lambda **_: gateway,
    )

    with TestClient(api) as client:
        start_response = client.post(
            "/api/council/live",
            json=_request(None).model_dump(mode="json"),
        )
        run_id = start_response.json()["run_id"]
        events_response = client.get(
            f"/api/council/live/{run_id}/events"
        )
        status_response = client.get(f"/api/council/live/{run_id}")
        invalid_cursor_response = client.get(
            f"/api/council/live/{run_id}/events",
            headers={"Last-Event-ID": "invalid"},
        )
        terminal_cancel_response = client.delete(
            f"/api/council/live/{run_id}"
        )

    assert start_response.status_code == 202
    assert start_response.json()["events_url"].endswith("/events")
    assert events_response.status_code == 200
    assert events_response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert "event: synthesis.delta" in events_response.text
    assert "event: run.completed" in events_response.text
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert invalid_cursor_response.status_code == 400
    assert terminal_cancel_response.status_code == 200
    assert (
        terminal_cancel_response.json()["cancellation_requested"]
        is False
    )


def test_openrouter_adapter_streams_deltas_and_usage() -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.closed = False
            self.items = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="Потоковый ")
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="ответ.")
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=4,
                        completion_tokens=2,
                        total_tokens=6,
                    ),
                ),
            ]

        def __iter__(self):
            return iter(self.items)

        def close(self) -> None:
            self.closed = True

    stream = FakeStream()
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return stream

    adapter = object.__new__(OpenRouterAdapter)
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )
    request = GatewayRequest(
        messages=[
            GatewayMessage(role="user", content="Вопрос"),
        ],
        model="test/model:free",
        provider="openrouter",
    )
    deltas: list[str] = []

    response = adapter.complete_stream(request, deltas.append)

    assert response.status == "success"
    assert response.content == "Потоковый ответ."
    assert response.usage.total_tokens == 6
    assert deltas == ["Потоковый ", "ответ."]
    assert calls[0]["stream"] is True
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert stream.closed is True
