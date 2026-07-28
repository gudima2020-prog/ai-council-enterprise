from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_lifespan_starts_and_stops_application() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_root_endpoint_inside_lifespan() -> None:
    with TestClient(app) as client:
        response = client.get("/")

        assert response.status_code == 200
        payload = response.json()

        assert payload["status"] == "running"
        assert "name" in payload
        assert "version" in payload
