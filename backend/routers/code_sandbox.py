from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from backend.api.dependencies import get_code_agent_service, get_code_sandbox_service, get_isolated_runtime_service
from backend.code_sandbox.schemas import (
    CodeSandboxApplyRequest,
    CodeSandboxApproval,
    CodeSandboxApprovalRequest,
    CodeSandboxCreateRequest,
    CodeSandboxInspectResponse,
    CodeSandboxSession,
    CodeSandboxStatus,
    CodeSandboxVerifyRequest,
    CodeAgentRun,
    CodeAgentRunRequest,
    CodeAgentRunResponse,
    CodeAgentStatus,
    IsolatedRuntimeRun,
    IsolatedRuntimeRunRequest,
    IsolatedRuntimeRunResponse,
    IsolatedRuntimeStatus,
)
from backend.code_sandbox.agent import CodeAgentService
from backend.code_sandbox.runtime import (
    IsolatedRuntimePolicyApprovalRequiredError,
    IsolatedRuntimePolicyDeniedError,
    IsolatedRuntimeService,
)
from backend.code_sandbox.service import (
    CodeSandboxApprovalInvalidError,
    CodeSandboxApprovalRequiredError,
    CodeSandboxConflictError,
    CodeSandboxError,
    CodeSandboxService,
)

router = APIRouter(tags=["code-sandbox"])


def _workspace_id(request: Request, requested: str | None = None) -> str | None:
    resolved = getattr(request.state, "workspace_context", None)
    current = resolved.get("workspace_id") if isinstance(resolved, dict) else None
    if requested is not None and current is not None and requested != current:
        raise HTTPException(status_code=409, detail="workspace_id запроса не совпадает с текущим X-Workspace-ID.")
    return current or requested


def _translate(exc: CodeSandboxError) -> HTTPException:
    if isinstance(
        exc,
        IsolatedRuntimePolicyApprovalRequiredError,
    ):
        return HTTPException(
            status_code=428,
            detail={
                "code": (
                    "RUNTIME_POLICY_APPROVAL_REQUIRED"
                ),
                "message": str(exc),
            },
        )

    if isinstance(
        exc,
        IsolatedRuntimePolicyDeniedError,
    ):
        return HTTPException(
            status_code=403,
            detail={
                "code": "RUNTIME_POLICY_DENIED",
                "message": str(exc),
            },
        )

    if isinstance(exc, CodeSandboxApprovalRequiredError):
        return HTTPException(status_code=428, detail={"code": "CODE_PATCH_APPROVAL_REQUIRED", "message": str(exc)})
    if isinstance(exc, CodeSandboxApprovalInvalidError):
        return HTTPException(status_code=403, detail={"code": "CODE_PATCH_APPROVAL_INVALID", "message": str(exc)})
    if isinstance(exc, CodeSandboxConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/code-sandbox/status", response_model=CodeSandboxStatus)
def code_sandbox_status() -> CodeSandboxStatus:
    return CodeSandboxStatus(git_available=CodeSandboxService.git_available())


@router.get("/code-sandbox/sessions", response_model=list[CodeSandboxSession])
def list_sessions(
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> list[CodeSandboxSession]:
    return service.list(_workspace_id(http_request), limit=limit)


@router.post("/code-sandbox/sessions", response_model=CodeSandboxSession, status_code=201)
async def create_session(
    payload: CodeSandboxCreateRequest,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxSession:
    try:
        return await service.create(payload, _workspace_id(http_request, payload.workspace_id))
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.get("/code-sandbox/sessions/{session_id}", response_model=CodeSandboxSession)
def get_session(
    session_id: str,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxSession:
    result = service.get(session_id, _workspace_id(http_request))
    if result is None:
        raise HTTPException(status_code=404, detail="Code Sandbox session не найден.")
    return result


@router.post("/code-sandbox/sessions/{session_id}/inspect", response_model=CodeSandboxInspectResponse)
async def inspect_patch(
    session_id: str,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxInspectResponse:
    try:
        return await service.inspect(session_id, _workspace_id(http_request))
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.post("/code-sandbox/sessions/{session_id}/verify", response_model=CodeSandboxSession)
async def verify_patch(
    session_id: str,
    payload: CodeSandboxVerifyRequest,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxSession:
    try:
        return await service.verify(session_id, _workspace_id(http_request), payload)
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.post("/code-sandbox/sessions/{session_id}/approval", response_model=CodeSandboxApproval, status_code=201)
async def approve_patch(
    session_id: str,
    payload: CodeSandboxApprovalRequest,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxApproval:
    try:
        return await service.approve(session_id, _workspace_id(http_request), payload.reason)
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.post("/code-sandbox/sessions/{session_id}/apply", response_model=CodeSandboxSession)
async def apply_patch(
    session_id: str,
    payload: CodeSandboxApplyRequest,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxSession:
    try:
        return await service.apply(session_id, _workspace_id(http_request), payload.approval_token)
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.get("/code-sandbox/sessions/{session_id}/patch")
def download_patch(
    session_id: str,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> Response:
    try:
        data = service.patch_bytes(session_id, _workspace_id(http_request))
    except CodeSandboxError as exc:
        raise _translate(exc) from exc
    return Response(
        content=data,
        media_type="text/x-diff; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.patch"'},
    )


@router.get("/code-sandbox/agent/status", response_model=CodeAgentStatus)
def code_agent_status() -> CodeAgentStatus:
    return CodeAgentStatus()


@router.get(
    "/code-sandbox/sessions/{session_id}/agent-runs",
    response_model=list[CodeAgentRun],
)
def list_agent_runs(
    session_id: str,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    service: CodeAgentService = Depends(get_code_agent_service),
) -> list[CodeAgentRun]:
    return service.list_for_session(session_id, _workspace_id(http_request), limit=limit)


@router.get(
    "/code-sandbox/agent-runs/{run_id}",
    response_model=CodeAgentRun,
)
def get_agent_run(
    run_id: str,
    http_request: Request,
    service: CodeAgentService = Depends(get_code_agent_service),
) -> CodeAgentRun:
    result = service.get(run_id, _workspace_id(http_request))
    if result is None:
        raise HTTPException(status_code=404, detail="Code Agent run не найден.")
    return result


@router.post(
    "/code-sandbox/sessions/{session_id}/agent-runs",
    response_model=CodeAgentRunResponse,
    status_code=201,
)
async def run_code_agent(
    session_id: str,
    payload: CodeAgentRunRequest,
    http_request: Request,
    service: CodeAgentService = Depends(get_code_agent_service),
) -> CodeAgentRunResponse:
    try:
        return await service.run(session_id, _workspace_id(http_request), payload)
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.get("/code-sandbox/runtime/status", response_model=IsolatedRuntimeStatus)
def isolated_runtime_status(
    service: IsolatedRuntimeService = Depends(get_isolated_runtime_service),
) -> IsolatedRuntimeStatus:
    return service.status()


@router.get(
    "/code-sandbox/sessions/{session_id}/runtime-runs",
    response_model=list[IsolatedRuntimeRun],
)
def list_runtime_runs(
    session_id: str,
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    service: IsolatedRuntimeService = Depends(get_isolated_runtime_service),
) -> list[IsolatedRuntimeRun]:
    return service.list_for_session(session_id, _workspace_id(http_request), limit=limit)


@router.get("/code-sandbox/runtime-runs/{run_id}", response_model=IsolatedRuntimeRun)
def get_runtime_run(
    run_id: str,
    http_request: Request,
    service: IsolatedRuntimeService = Depends(get_isolated_runtime_service),
) -> IsolatedRuntimeRun:
    result = service.get(run_id, _workspace_id(http_request))
    if result is None:
        raise HTTPException(status_code=404, detail="Runtime run не найден.")
    return result


@router.post(
    "/code-sandbox/sessions/{session_id}/runtime-runs",
    response_model=IsolatedRuntimeRunResponse,
    status_code=201,
)
async def run_isolated_runtime(
    session_id: str,
    payload: IsolatedRuntimeRunRequest,
    http_request: Request,
    service: IsolatedRuntimeService = Depends(get_isolated_runtime_service),
) -> IsolatedRuntimeRunResponse:
    try:
        return await service.run(session_id, _workspace_id(http_request), payload)
    except CodeSandboxError as exc:
        raise _translate(exc) from exc


@router.get("/code-sandbox/runtime-runs/{run_id}/artifacts")
async def download_runtime_artifacts(
    run_id: str,
    http_request: Request,
    service: IsolatedRuntimeService = Depends(get_isolated_runtime_service),
) -> Response:
    try:
        path = await service.artifact_zip_path(
            run_id,
            _workspace_id(http_request),
        )
    except CodeSandboxError as exc:
        raise _translate(exc) from exc
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-artifacts.zip"'},
    )


@router.delete("/code-sandbox/sessions/{session_id}", response_model=CodeSandboxSession)
async def close_session(
    session_id: str,
    http_request: Request,
    service: CodeSandboxService = Depends(get_code_sandbox_service),
) -> CodeSandboxSession:
    try:
        return await service.close(session_id, _workspace_id(http_request))
    except CodeSandboxError as exc:
        raise _translate(exc) from exc
