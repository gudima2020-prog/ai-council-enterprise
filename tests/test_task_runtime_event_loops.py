from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_application_can_restart_lifespan_multiple_times() -> None:
    """
    Regression test: each TestClient context may use a different asyncio loop.
    Queue/scheduler objects must not be reused between them.

    Keep strong references to earlier queues while comparing identity.
    Comparing only integer id() values after an object is released is invalid:
    CPython may legally reuse that memory address for a later fresh object.
    """
    queues: list[object] = []

    for _ in range(3):
        with TestClient(app) as client:
            response = client.get("/api/task-scheduler/status")

            assert response.status_code == 200
            assert response.json()["running"] is True

            container = app.state.container
            assert container.task_queue is not None
            current_queue = container.task_queue
            assert all(
                current_queue is not previous_queue
                for previous_queue in queues
            )
            queues.append(current_queue)

        assert app.state.container.task_queue is None
        assert app.state.container.task_scheduler is None

    assert len(queues) == 3
