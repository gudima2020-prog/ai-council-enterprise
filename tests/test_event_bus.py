import pytest
from backend.core.events import Event, EventBus

@pytest.mark.asyncio
async def test_exact_subscription():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event.event_type)

    bus.subscribe("chat.message.created", handler)
    result = await bus.publish(Event(event_type="chat.message.created", source="test"))

    assert received == ["chat.message.created"]
    assert result.completed_handlers == 1

@pytest.mark.asyncio
async def test_wildcard_and_failure_isolation():
    bus = EventBus()
    received = []

    def bad(event: Event):
        raise RuntimeError("boom")

    def good(event: Event):
        received.append(event.id)

    bus.subscribe("system.*", bad)
    bus.subscribe("system.*", good)
    event = Event(event_type="system.test.created", source="test")
    result = await bus.publish(event)

    assert received == [event.id]
    assert result.failed_handlers == 1
    assert result.completed_handlers == 1
