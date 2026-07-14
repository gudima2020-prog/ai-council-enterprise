from typing import Any
from fastapi import APIRouter, Query
from backend.core.events import Event, event_bus

router = APIRouter(tags=["events"])

@router.get("/events/subscriptions")
def event_subscriptions() -> dict[str, list[str]]:
    return event_bus.subscriptions()

@router.get("/events/history")
def event_history(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
    return event_bus.history(limit=limit)

@router.post("/events/test")
async def publish_test_event() -> dict[str, Any]:
    event = Event(
        event_type="system.test.created",
        source="api",
        payload={"message": "Event Bus is working"}
    )
    result = await event_bus.publish(event)
    return {"event": event.to_dict(), "delivery": result.__dict__}
