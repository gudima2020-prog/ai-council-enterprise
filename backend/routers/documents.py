from __future__ import annotations

import json
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from backend.api.dependencies import (
    get_document_extraction_service,
    get_document_registry_service,
    get_workspace_policy_service,
)
from backend.control_center.governance_schemas import (
    HumanControlPermission,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    HumanControlSecurityError,
    enforce_workspace_value,
    require_permission,
    security_http_exception,
)
from backend.documents.extraction_service import (
    DocumentExtractionConflictError,
    DocumentExtractionNotFoundError,
    DocumentExtractionService,
    DocumentExtractionServiceError,
)
from backend.documents.intake import (
    DocumentIntakeError,
    DocumentIntakePolicy,
    DocumentIntakeRequest,
)
from backend.documents.service import (
    DocumentNotFoundError,
    DocumentRegistryError,
    DocumentRegistryService,
    DocumentWorkspaceError,
)
from backend.documents.storage import DocumentStorageError
from backend.services.workspace_policy import WorkspacePolicyService


router = APIRouter(
    prefix="/workspaces/{workspace_id}/documents",
    tags=["documents"],
)

_UPLOAD_READ_CHUNK_BYTES = 64 * 1024
_MAX_UPLOAD_BYTES = DocumentIntakePolicy().max_file_bytes
_MAX_METADATA_JSON_CHARS = 32_768


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            DocumentNotFoundError,
            DocumentWorkspaceError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(exc, DocumentIntakeError):
        if exc.code == "DOCUMENT_TOO_LARGE":
            return HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=str(exc),
            )
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )

    if isinstance(exc, DocumentStorageError):
        if exc.code in {
            "DOCUMENT_STORAGE_MISSING",
            "DOCUMENT_STORAGE_SIZE_MISMATCH",
            "DOCUMENT_STORAGE_HASH_MISMATCH",
        }:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Managed document storage operation failed.",
        )

    if isinstance(
        exc,
        DocumentExtractionNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        DocumentExtractionConflictError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        DocumentExtractionServiceError,
    ):
        return HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        )

    if isinstance(exc, DocumentRegistryError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Document registry operation failed.",
    )


def _bound_workspace(
    workspace_id: str,
    principal: HumanControlPrincipal,
) -> str:
    try:
        resolved = enforce_workspace_value(
            workspace_id,
            principal,
        )
    except HumanControlSecurityError as exc:
        raise security_http_exception(exc) from exc

    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A Workspace is required.",
        )
    return resolved


def _bound_actor(
    principal: HumanControlPrincipal,
) -> str | None:
    if not principal.authenticated:
        return None

    actor_id = principal.actor_id or principal.identity_id
    if not actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Authenticated Human Control principal has no actor binding."
            ),
        )
    return actor_id


def _parse_metadata(metadata_json: str) -> dict[str, Any]:
    try:
        value = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="metadata_json must contain valid JSON.",
        ) from exc

    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="metadata_json must contain a JSON object.",
        )
    return value


async def _read_bounded_upload(
    upload: UploadFile,
) -> bytes:
    chunks: list[bytes] = []
    total = 0

    try:
        while True:
            remaining = _MAX_UPLOAD_BYTES - total + 1
            chunk = await upload.read(
                min(_UPLOAD_READ_CHUNK_BYTES, remaining)
            )
            if not chunk:
                break

            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=(
                        status.HTTP_413_CONTENT_TOO_LARGE
                    ),
                    detail=(
                        "Document exceeds the configured upload size limit."
                    ),
                )
    finally:
        await upload.close()

    return b"".join(chunks)


@router.post("")
async def upload_document(
    workspace_id: str,
    response: Response,
    file: UploadFile = File(...),
    metadata_json: str = Form(
        default="{}",
        max_length=_MAX_METADATA_JSON_CHARS,
    ),
    service: DocumentRegistryService = Depends(
        get_document_registry_service
    ),
    policy_service: WorkspacePolicyService = Depends(
        get_workspace_policy_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_UPLOAD.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    actor_id = _bound_actor(principal)

    if not file.filename or not file.filename.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded document filename is required.",
        )

    metadata = _parse_metadata(metadata_json)
    content = await _read_bounded_upload(file)

    try:
        policy = policy_service.get_effective_policy(
            workspace_id
        )
        result = await service.upload(
            request=DocumentIntakeRequest(
                workspace_id=workspace_id,
                filename=file.filename,
                content_type=file.content_type,
                classification=policy.data_classification,
            ),
            content=content,
            actor_id=actor_id,
            metadata=metadata,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    response.status_code = (
        status.HTTP_201_CREATED
        if result.created
        else status.HTTP_200_OK
    )
    return {
        "created": result.created,
        "restored": result.restored,
        "storage_created": result.storage_created,
        "document": result.record.to_public_dict(),
    }


@router.get("")
def list_documents(
    workspace_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: DocumentRegistryService = Depends(
        get_document_registry_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )

    try:
        records = service.list(
            workspace_id=workspace_id,
            include_deleted=False,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "workspace_id": workspace_id,
        "items": [
            record.to_public_dict()
            for record in records
        ],
        "limit": limit,
        "offset": offset,
    }



@router.post("/{document_id}/extractions")
async def extract_document(
    workspace_id: str,
    document_id: str,
    response: Response,
    service: DocumentExtractionService = Depends(
        get_document_extraction_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_EXTRACT.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    actor_id = _bound_actor(principal)

    try:
        result = await service.extract(
            document_id=document_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    if result.record.status == "failed":
        if result.record.error_code in {
            "DOCUMENT_STORAGE_MISSING",
            "DOCUMENT_STORAGE_SIZE_MISMATCH",
            "DOCUMENT_STORAGE_HASH_MISMATCH",
        }:
            response.status_code = (
                status.HTTP_409_CONFLICT
            )
        elif result.record.error_code == (
            "DOCUMENT_EXTRACTION_INTERNAL_ERROR"
        ):
            response.status_code = (
                status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        else:
            response.status_code = (
                status.HTTP_422_UNPROCESSABLE_CONTENT
            )
    else:
        response.status_code = (
            status.HTTP_201_CREATED
            if result.created
            else status.HTTP_200_OK
        )

    return {
        "created": result.created,
        "reused": result.reused,
        "extraction": result.record.to_public_dict(),
    }


@router.get("/{document_id}/extractions")
def list_document_extractions(
    workspace_id: str,
    document_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: DocumentExtractionService = Depends(
        get_document_extraction_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    try:
        items = service.list_runs(
            document_id=document_id,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "items": [
            item.to_public_dict()
            for item in items
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{document_id}/extractions/{run_id}")
def get_document_extraction(
    workspace_id: str,
    document_id: str,
    run_id: str,
    service: DocumentExtractionService = Depends(
        get_document_extraction_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    try:
        record = service.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return record.to_public_dict()


@router.get(
    "/{document_id}/extractions/{run_id}/units"
)
def list_document_extraction_units(
    workspace_id: str,
    document_id: str,
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: DocumentExtractionService = Depends(
        get_document_extraction_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    try:
        items = service.list_units(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "run_id": run_id,
        "items": [
            item.to_public_dict()
            for item in items
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{document_id}/extractions/{run_id}/chunks"
)
def list_document_extraction_chunks(
    workspace_id: str,
    document_id: str,
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: DocumentExtractionService = Depends(
        get_document_extraction_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    try:
        items = service.list_chunks(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "run_id": run_id,
        "items": [
            item.to_public_dict()
            for item in items
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{document_id}/events")
def get_document_events(
    workspace_id: str,
    document_id: str,
    service: DocumentRegistryService = Depends(
        get_document_registry_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_AUDIT.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )

    try:
        items = service.events(
            document_id=document_id,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "items": items,
    }


@router.get("/{document_id}")
def get_document(
    workspace_id: str,
    document_id: str,
    service: DocumentRegistryService = Depends(
        get_document_registry_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_VIEW.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )

    try:
        record = service.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return record.to_public_dict()


@router.delete("/{document_id}")
async def delete_document(
    workspace_id: str,
    document_id: str,
    service: DocumentRegistryService = Depends(
        get_document_registry_service
    ),
    principal: HumanControlPrincipal = Depends(
        require_permission(
            HumanControlPermission.DOCUMENT_DELETE.value
        )
    ),
) -> dict[str, Any]:
    workspace_id = _bound_workspace(
        workspace_id,
        principal,
    )
    actor_id = _bound_actor(principal)

    try:
        result = await service.delete(
            document_id=document_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc

    return {
        "deleted": result.deleted,
        "storage_deleted": result.storage_deleted,
        "derived_deleted": dict(
            result.derived_deleted
        ),
        "document": result.record.to_public_dict(),
    }
