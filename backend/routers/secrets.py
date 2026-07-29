from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_container
from backend.control_center.security import (
    HumanControlPrincipal,
    bind_actor,
    bind_workspace,
    enforce_workspace_value,
    require_permission,
)
from backend.core.container import AppContainer
from backend.secrets.schemas import (
    SecretAccessEvaluationRequest,
    SecretAccessPolicyCreate,
    SecretAccessPolicyUpdate,
    SecretAccessSettingsUpsert,
    SecretCreate,
    SecretLeaseCreate,
    SecretLeaseRevokeRequest,
    SecretProviderCreate,
    SecretProviderUpdate,
    SecretRotateRequest,
    SecretStateRequest,
    SecretVerifyRequest,
    SecretRotationPolicyUpsert,
    SecretHealthAlertDecision,
    SecretLifecycleScanRequest,
    SecretRotationRunCreate,
    SecretRotationRunDecision,
)
from backend.secrets.service import (
    SecretConflict,
    SecretManagerError,
    SecretManagerService,
    SecretNotFound,
)

router = APIRouter(tags=["secret-management"])


def service(container: AppContainer = Depends(get_container)) -> SecretManagerService:
    manager = container.secret_manager_service
    if manager is None:
        raise HTTPException(status_code=503, detail="Secret Manager is not started.")
    return manager




def require_secret_permission(permission: str):
    async def dependency(
        principal: HumanControlPrincipal = Depends(require_permission(permission)),
    ) -> HumanControlPrincipal:
        if not principal.authenticated:
            raise HTTPException(
                status_code=401,
                detail="Secret Management requires an authenticated operator or API token.",
            )
        return principal

    return dependency

def _raise(exc: SecretManagerError) -> None:
    if isinstance(exc, SecretNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SecretConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/secrets/status")
def status(
    manager: SecretManagerService = Depends(service),
    _: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    return manager.status()


@router.post("/secret-providers")
async def create_provider(
    request: SecretProviderCreate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal, field_name="created_by")
    request = bind_workspace(request, principal)
    try:
        return await manager.create_provider(request)
    except SecretManagerError as exc:
        _raise(exc)


@router.get("/secret-providers")
def list_providers(
    workspace_id: str | None = Query(default=None),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {"providers": manager.list_providers(workspace_id=workspace_id)}


@router.patch("/secret-providers/{provider_id}")
async def update_provider(
    provider_id: str,
    request: SecretProviderUpdate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.update_provider(
            provider_id, request, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secret-providers/{provider_id}/health")
async def provider_health(
    provider_id: str,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    try:
        return await manager.check_provider(
            provider_id,
            actor_id=principal.actor_id or "legacy-operator",
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets")
async def create_secret(
    request: SecretCreate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal, field_name="created_by")
    request = bind_workspace(request, principal)
    try:
        return await manager.create_secret(request)
    except (SecretManagerError, RuntimeError) as exc:
        if isinstance(exc, SecretManagerError):
            _raise(exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/secrets")
def list_secrets(
    workspace_id: str | None = Query(default=None),
    provider_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "secrets": manager.list_secrets(
            workspace_id=workspace_id,
            provider_key=provider_key,
            status=status,
        )
    }


@router.get("/secrets/{secret_id}")
def get_secret(
    secret_id: str,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    try:
        return manager.get_secret(
            secret_id, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.get("/secrets/{secret_id}/versions")
def versions(
    secret_id: str,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    try:
        return {
            "versions": manager.list_versions(
                secret_id, expected_workspace_id=principal.workspace_id
            )
        }
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets/{secret_id}/rotate")
async def rotate(
    secret_id: str,
    request: SecretRotateRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.rotate")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.rotate_secret(
            secret_id, request, expected_workspace_id=principal.workspace_id
        )
    except (SecretManagerError, RuntimeError) as exc:
        if isinstance(exc, SecretManagerError):
            _raise(exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/secrets/{secret_id}/disable")
async def disable(
    secret_id: str,
    request: SecretStateRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.set_secret_state(
            secret_id,
            enabled=False,
            request=request,
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets/{secret_id}/enable")
async def enable(
    secret_id: str,
    request: SecretStateRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.set_secret_state(
            secret_id,
            enabled=True,
            request=request,
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets/{secret_id}/verify")
async def verify(
    secret_id: str,
    request: SecretVerifyRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    request = bind_actor(request, principal)
    return await manager.verify_secret(
        secret_id,
        actor_id=request.actor_id,
        purpose=request.purpose,
        auth_method=principal.auth_method if principal.authenticated else request.auth_method,
        source=request.source,
        workspace_id=principal.workspace_id,
    )


@router.get("/secret-access-events")
def access_events(
    workspace_id: str | None = Query(default=None),
    secret_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "events": manager.list_access_events(
            workspace_id=workspace_id,
            secret_id=secret_id,
            actor_id=actor_id,
            limit=limit,
        )
    }


@router.get("/secret-access/settings")
def get_secret_access_settings(
    workspace_id: str | None = Query(default=None),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return manager.get_access_settings(workspace_id=workspace_id)


@router.put("/secret-access/settings")
async def put_secret_access_settings(
    request: SecretAccessSettingsUpsert,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    request = bind_workspace(request, principal)
    try:
        return await manager.upsert_access_settings(request)
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secret-access/policies")
async def create_secret_access_policy(
    request: SecretAccessPolicyCreate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal, field_name="created_by")
    request = bind_workspace(request, principal)
    try:
        return await manager.create_access_policy(request)
    except SecretManagerError as exc:
        _raise(exc)


@router.get("/secret-access/policies")
def list_secret_access_policies(
    workspace_id: str | None = Query(default=None),
    include_global: bool = Query(default=True),
    enabled: bool | None = Query(default=None),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "policies": manager.list_access_policies(
            workspace_id=workspace_id,
            include_global=include_global,
            enabled=enabled,
        )
    }


@router.patch("/secret-access/policies/{policy_id}")
async def update_secret_access_policy(
    policy_id: str,
    request: SecretAccessPolicyUpdate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.update_access_policy(
            policy_id,
            request,
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets/{secret_id}/access-evaluate")
async def evaluate_secret_access(
    secret_id: str,
    request: SecretAccessEvaluationRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    request = bind_actor(request, principal)
    request = bind_workspace(request, principal)
    try:
        return await manager.evaluate_access(
            secret_id,
            request,
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secret-leases")
async def create_secret_lease(
    request: SecretLeaseCreate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    request = bind_actor(request, principal, field_name="created_by")
    request = bind_workspace(request, principal)
    try:
        return await manager.issue_lease(request)
    except SecretManagerError as exc:
        _raise(exc)


@router.get("/secret-leases")
def list_secret_leases(
    workspace_id: str | None = Query(default=None),
    secret_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    consumer_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "leases": manager.list_leases(
            workspace_id=workspace_id,
            secret_id=secret_id,
            actor_id=actor_id,
            consumer_type=consumer_type,
            status=status,
            limit=limit,
        )
    }


@router.post("/secret-leases/{lease_id}/revoke")
async def revoke_secret_lease(
    lease_id: str,
    request: SecretLeaseRevokeRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.revoke_lease(
            lease_id,
            request,
            expected_workspace_id=principal.workspace_id,
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secret-leases/reconcile")
async def reconcile_secret_leases(
    manager: SecretManagerService = Depends(service),
    _: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    return await manager.reconcile_leases()



@router.get("/secret-lifecycle/status")
def secret_lifecycle_status(
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    return manager.status()


@router.get("/secrets/{secret_id}/rotation-policy")
def get_rotation_policy(
    secret_id: str,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    try:
        return {
            "policy": manager.get_rotation_policy(
                secret_id, expected_workspace_id=principal.workspace_id
            )
        }
    except SecretManagerError as exc:
        _raise(exc)


@router.put("/secrets/{secret_id}/rotation-policy")
async def put_rotation_policy(
    secret_id: str,
    request: SecretRotationPolicyUpsert,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.rotate")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.upsert_rotation_policy(
            secret_id, request, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secrets/{secret_id}/rotation-runs")
async def create_rotation_run(
    secret_id: str,
    request: SecretRotationRunCreate,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.rotate")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.create_rotation_run(
            secret_id, request, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.get("/secrets/{secret_id}/rotation-runs")
def secret_rotation_runs(
    secret_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.view")),
):
    return {
        "runs": manager.list_rotation_runs(
            secret_id=secret_id,
            workspace_id=principal.workspace_id,
            status=status,
            limit=limit,
        )
    }


@router.post("/secret-rotation-runs/{run_id}/decision")
async def decide_rotation_run(
    run_id: str,
    request: SecretRotationRunDecision,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.rotate")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.decide_rotation_run(
            run_id, request, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)


@router.post("/secret-lifecycle/scan")
async def scan_secret_lifecycle(
    request: SecretLifecycleScanRequest,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    request = bind_actor(request, principal)
    request = bind_workspace(request, principal)
    return await manager.scan_lifecycle(request)


@router.get("/secret-health-checks")
def secret_health_checks(
    workspace_id: str | None = Query(default=None),
    secret_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "checks": manager.list_health_checks(
            workspace_id=workspace_id,
            secret_id=secret_id,
            status=status,
            limit=limit,
        )
    }


@router.get("/secret-health-alerts")
def secret_health_alerts(
    workspace_id: str | None = Query(default=None),
    secret_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.audit")),
):
    workspace_id = enforce_workspace_value(workspace_id, principal)
    return {
        "alerts": manager.list_health_alerts(
            workspace_id=workspace_id,
            secret_id=secret_id,
            status=status,
            limit=limit,
        )
    }


@router.post("/secret-health-alerts/{alert_id}/decision")
async def decide_secret_health_alert(
    alert_id: str,
    request: SecretHealthAlertDecision,
    manager: SecretManagerService = Depends(service),
    principal: HumanControlPrincipal = Depends(require_secret_permission("secret.manage")),
):
    request = bind_actor(request, principal)
    try:
        return await manager.decide_health_alert(
            alert_id, request, expected_workspace_id=principal.workspace_id
        )
    except SecretManagerError as exc:
        _raise(exc)
