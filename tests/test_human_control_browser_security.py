from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center.auth import HumanControlAuthService
from backend.control_center.auth_schemas import (
    AuthEnforcementMode,
    HumanControlAuthPolicyUpsert,
    HumanControlIdentityCreate,
)
from backend.control_center.browser_schemas import (
    HumanControlBrowserLoginRequest,
    HumanControlBrowserPolicyUpsert,
    HumanControlTrustedClientCreate,
)
from backend.control_center.browser_security import (
    HumanControlBrowserSecurityService,
    normalize_origin,
)
from backend.control_center.security import (
    HumanControlAuthMiddleware,
    HumanControlPrincipal,
    require_permission,
)
from backend.control_center.service import HumanControlError
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

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

    with scope() as session:
        session.add(database_models.WorkspaceModel(id="workspace_browser", name="Browser"))
    return scope


async def make_services():
    scope = make_scope()
    events = EventBus()
    auth = HumanControlAuthService(event_bus=events, session_factory=scope)
    browser = HumanControlBrowserSecurityService(
        event_bus=events,
        auth_service=auth,
        session_factory=scope,
    )
    await auth.create_identity(
        HumanControlIdentityCreate(
            actor_id="alice",
            username="alice",
            display_name="Alice",
            password="correct-horse-battery-staple",
            created_by="bootstrap",
        )
    )
    await auth.upsert_policy(
        HumanControlAuthPolicyUpsert(
            enabled=True,
            enforcement_mode=AuthEnforcementMode.ENFORCE,
            actor_id="alice",
        )
    )
    await browser.upsert_policy(
        HumanControlBrowserPolicyUpsert(
            enabled=True,
            cookie_secure=False,
            actor_id="alice",
        )
    )
    await browser.create_trusted_client(
        HumanControlTrustedClientCreate(
            client_key="control-ui",
            name="Control UI",
            allowed_origins=["http://testserver/"],
            created_by="alice",
        )
    )
    return auth, browser


def test_origin_normalization() -> None:
    assert normalize_origin("HTTPS://Example.COM:443/") == "https://example.com"
    assert normalize_origin("http://localhost:5173") == "http://localhost:5173"
    with pytest.raises(HumanControlError):
        normalize_origin("https://example.com/path")


@pytest.mark.asyncio
async def test_browser_login_creates_cookie_session_and_csrf_secret() -> None:
    _, browser = await make_services()
    result = await browser.login(
        HumanControlBrowserLoginRequest(
            username="alice",
            password="correct-horse-battery-staple",
            client_key="control-ui",
        ),
        origin="http://testserver",
        client_ip="127.0.0.1",
        user_agent="pytest-browser",
    )
    assert result["session"]["auth_method"] == "browser"
    payload = browser.authenticate_cookie(
        result["session_token"],
        workspace_id=None,
        origin="http://testserver",
        client_ip="127.0.0.1",
        user_agent="pytest-browser",
    )
    assert payload["actor_id"] == "alice"
    browser.validate_csrf(
        session_id=payload["credential_id"],
        csrf_token=result["csrf_token"],
        method="POST",
        policy=browser.effective_policy(),
    )
    with pytest.raises(HumanControlError):
        browser.validate_csrf(
            session_id=payload["credential_id"],
            csrf_token="wrong",
            method="POST",
            policy=browser.effective_policy(),
        )


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected() -> None:
    _, browser = await make_services()
    with pytest.raises(HumanControlError):
        await browser.login(
            HumanControlBrowserLoginRequest(
                username="alice",
                password="correct-horse-battery-staple",
                client_key="control-ui",
            ),
            origin="https://evil.example",
            client_ip="127.0.0.1",
            user_agent="pytest-browser",
        )


@pytest.mark.asyncio
async def test_browser_middleware_requires_matching_csrf_cookie_and_header() -> None:
    auth, browser = await make_services()
    login = await browser.login(
        HumanControlBrowserLoginRequest(
            username="alice",
            password="correct-horse-battery-staple",
            client_key="control-ui",
        ),
        origin="http://testserver",
        client_ip="testclient",
        user_agent="testclient",
    )

    app = FastAPI()
    app.state.container = SimpleNamespace(
        human_control_auth_service=auth,
        human_control_browser_security_service=browser,
        human_control_governance_service=None,
    )
    app.add_middleware(HumanControlAuthMiddleware)

    @app.post("/api/human-control/secure")
    async def secure(
        principal: HumanControlPrincipal = Depends(
            require_permission("human_control.view")
        ),
    ):
        return {"actor_id": principal.actor_id}

    client = TestClient(app)
    client.cookies.set("hc_browser_session", login["session_token"])
    client.cookies.set("hc_csrf", login["csrf_token"])
    headers = {"Origin": "http://testserver", "User-Agent": "testclient"}

    assert client.post("/api/human-control/secure", headers=headers).status_code == 403
    response = client.post(
        "/api/human-control/secure",
        headers={**headers, "X-CSRF-Token": login["csrf_token"]},
    )
    assert response.status_code == 200
    assert response.json()["actor_id"] == "alice"
