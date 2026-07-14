from fastapi.testclient import TestClient

from backend.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_context_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/context")

    assert response.status_code == 200
    body = response.json()
    assert "workspace_id" in body
    assert "source" in body


def test_readiness_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/system/readiness")

    assert response.status_code in {200, 503}
    body = response.json()
    assert "checks" in body
    assert "database" in body["checks"]
    assert "models" in body["checks"]
    assert "plugins" in body["checks"]
