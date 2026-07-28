from fastapi.testclient import TestClient


def test_secrets_router_is_imported_and_registered() -> None:
    from backend.main import app

    # FastAPI versions that use lazy/included-router wrappers may expose
    # objects in app.routes without a public ``path`` attribute. Exercising
    # the ASGI application is a stable way to prove that the route is
    # registered. Secret Management requires authentication, so 401 is a
    # valid response here; 404 means the router was not mounted.
    with TestClient(app) as client:
        response = client.get("/api/secrets/status")

    assert response.status_code != 404
