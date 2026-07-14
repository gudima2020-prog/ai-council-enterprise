from typing import Any

from fastapi import APIRouter, Request

from backend.core.workspace_context import get_workspace_context

router = APIRouter(tags=["context"])


@router.get("/context")
def read_context(request: Request) -> dict[str, Any]:
    resolved = getattr(request.state, "workspace_context", None)

    if resolved is None:
        context = get_workspace_context()

        if context is None:
            return {
                "workspace_id": None,
                "source": "none",
                "workspace": None,
                "policy": None,
            }

        return {
            "workspace_id": context.workspace_id,
            "source": context.source,
            "workspace": None,
            "policy": context.policy,
        }

    return resolved
