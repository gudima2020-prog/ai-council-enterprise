from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    model_validator,
)

from backend.api.dependencies import get_document_ai_analysis_service
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.documents.ai_analysis import (
    DocumentAIAnalysisError,
    DocumentAIWorkflow,
)
from backend.documents.ai_context import (
    DocumentContextSelection,
    DocumentContextSourceKind,
    DocumentContextSourceRef,
)
from backend.documents.ai_service import (
    DocumentAIAnalysisService,
    DocumentAIApprovalCredentials,
    DocumentAIRequestSpec,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/document-ai",
    tags=["document-ai"],
)

_PRIVATE_NO_STORE = "private, no-store"


class DocumentAISourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    source_kind: DocumentContextSourceKind
    source_id: str = Field(..., min_length=1, max_length=64)


class DocumentAIPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: DocumentAIWorkflow
    question: SecretStr | None = None
    sources: list[DocumentAISourceRequest] = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=255)
    reviewer_provider: str = Field(..., min_length=1, max_length=64)
    reviewer_model: str = Field(..., min_length=1, max_length=255)
    max_output_tokens: StrictInt = Field(default=1_000, ge=1, le=32_768)
    reviewer_max_output_tokens: StrictInt = Field(
        default=512,
        ge=1,
        le=8_192,
    )
    retention_days: StrictInt | None = Field(
        default=None,
        ge=1,
        le=3650,
    )

    @model_validator(mode="after")
    def validate_workflow(self) -> DocumentAIPreflightRequest:
        if self.workflow is DocumentAIWorkflow.QUESTION:
            if (
                self.question is None
                or not self.question.get_secret_value().strip()
            ):
                raise ValueError("question workflow requires a question")
        elif self.question is not None:
            raise ValueError("summary workflow must not include a question")
        return self


class DocumentAIExecuteRequest(DocumentAIPreflightRequest):
    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    external_provider_acknowledged: StrictBool = False
    primary_approval_id: str | None = Field(default=None, max_length=255)
    primary_approval_token: SecretStr | None = None
    reviewer_approval_id: str | None = Field(default=None, max_length=255)
    reviewer_approval_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_approval_pairs(self) -> DocumentAIExecuteRequest:
        for stage in ("primary", "reviewer"):
            approval_id = getattr(self, f"{stage}_approval_id")
            token = getattr(self, f"{stage}_approval_token")
            if (approval_id is None) != (token is None):
                raise ValueError(
                    f"{stage} approval id and token must be supplied together"
                )
        return self


def _bound_workspace(
    workspace_id: str,
    principal: HumanControlPrincipal,
) -> str:
    try:
        resolved = enforce_workspace_value(workspace_id, principal)
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A Workspace is required.",
        )
    return resolved


def _bound_actor(principal: HumanControlPrincipal) -> str | None:
    if not principal.authenticated:
        return None
    actor_id = principal.actor_id or principal.identity_id
    if not actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated principal has no actor binding.",
        )
    return actor_id


def _spec(
    workspace_id: str,
    request: DocumentAIPreflightRequest,
) -> DocumentAIRequestSpec:
    return DocumentAIRequestSpec(
        workspace_id=workspace_id,
        workflow=request.workflow,
        selection=DocumentContextSelection(
            workspace_id=workspace_id,
            sources=tuple(
                DocumentContextSourceRef(
                    document_id=item.document_id,
                    run_id=item.run_id,
                    source_kind=item.source_kind,
                    source_id=item.source_id,
                )
                for item in request.sources
            ),
        ),
        provider=request.provider,
        model=request.model,
        reviewer_provider=request.reviewer_provider,
        reviewer_model=request.reviewer_model,
        question=(
            request.question.get_secret_value()
            if request.question is not None
            else None
        ),
        max_output_tokens=request.max_output_tokens,
        reviewer_max_output_tokens=request.reviewer_max_output_tokens,
        retention_days=request.retention_days,
    )


def _credentials(
    approval_id: str | None,
    token: SecretStr | None,
) -> DocumentAIApprovalCredentials | None:
    if approval_id is None or token is None:
        return None
    return DocumentAIApprovalCredentials(
        approval_id=approval_id,
        token=token.get_secret_value(),
    )


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentAIAnalysisError):
        if exc.code in {
            "DOCUMENT_AI_RUN_NOT_FOUND",
            "DOCUMENT_AI_SOURCE_NOT_FOUND",
        }:
            code = status.HTTP_404_NOT_FOUND
        elif exc.code in {
            "DOCUMENT_AI_IDEMPOTENCY_CONFLICT",
            "DOCUMENT_AI_RUN_CONFLICT",
            "DOCUMENT_AI_CONTEXT_CHANGED",
        }:
            code = status.HTTP_409_CONFLICT
        elif exc.code == "DOCUMENT_AI_EXTERNAL_ACK_REQUIRED":
            code = status.HTTP_428_PRECONDITION_REQUIRED
        elif exc.code == "DOCUMENT_AI_POLICY_DENIED":
            code = status.HTTP_403_FORBIDDEN
        else:
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return HTTPException(
            status_code=code,
            detail={
                "code": exc.code,
                "message": str(exc),
                "details": dict(exc.details),
            },
        )
    if isinstance(exc, (TypeError, ValueError)):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Document AI operation failed safely.",
    )


@router.post("/preflight")
def preflight_document_ai(
    workspace_id: str,
    request: DocumentAIPreflightRequest,
    response: Response,
    service: DocumentAIAnalysisService = Depends(
        get_document_ai_analysis_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.DOCUMENT_AI.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        result = service.preflight(spec=_spec(workspace_id, request))
    except Exception as exc:
        raise _translate_error(exc) from exc
    response.headers["Cache-Control"] = _PRIVATE_NO_STORE
    return result.to_public_dict()


@router.post("/runs")
async def execute_document_ai(
    workspace_id: str,
    request: DocumentAIExecuteRequest,
    response: Response,
    service: DocumentAIAnalysisService = Depends(
        get_document_ai_analysis_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.DOCUMENT_AI.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    actor_id = _bound_actor(principal)
    try:
        result = await service.execute(
            spec=_spec(workspace_id, request),
            idempotency_key=request.idempotency_key,
            external_provider_acknowledged=(
                request.external_provider_acknowledged
            ),
            actor_id=actor_id,
            primary_approval=_credentials(
                request.primary_approval_id,
                request.primary_approval_token,
            ),
            reviewer_approval=_credentials(
                request.reviewer_approval_id,
                request.reviewer_approval_token,
            ),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    if result.approval_required:
        response.status_code = status.HTTP_202_ACCEPTED
    elif result.record.row.status in {"failed", "rejected"}:
        response.status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif result.created:
        response.status_code = status.HTTP_201_CREATED
    response.headers["Cache-Control"] = _PRIVATE_NO_STORE
    return result.to_public_dict(include_content=True)


@router.get("/runs")
def list_document_ai_runs(
    workspace_id: str,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: DocumentAIAnalysisService = Depends(
        get_document_ai_analysis_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.DOCUMENT_AI.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        items = service.list(
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    response.headers["Cache-Control"] = _PRIVATE_NO_STORE
    return {
        "workspace_id": workspace_id,
        "items": [item.to_public_dict() for item in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/runs/{analysis_run_id}")
def get_document_ai_run(
    workspace_id: str,
    analysis_run_id: str,
    response: Response,
    include_content: bool = Query(default=False),
    service: DocumentAIAnalysisService = Depends(
        get_document_ai_analysis_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.DOCUMENT_AI.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        record = service.get(
            analysis_run_id=analysis_run_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    response.headers["Cache-Control"] = _PRIVATE_NO_STORE
    return record.to_public_dict(include_content=include_content)


@router.post("/retention/purge")
async def purge_document_ai_retention(
    workspace_id: str,
    service: DocumentAIAnalysisService = Depends(
        get_document_ai_analysis_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(HumanControlPermission.RETENTION_MANAGE.value)
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(workspace_id, principal)
    try:
        return await service.purge_expired(
            workspace_id=workspace_id,
            actor_id=_bound_actor(principal),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


__all__ = ["router"]
