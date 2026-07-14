from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkspaceRequestContext:
    workspace_id: str | None
    source: str
    policy: dict[str, Any] | None = None


_workspace_context: ContextVar[WorkspaceRequestContext | None] = ContextVar(
    "workspace_request_context",
    default=None,
)


def set_workspace_context(
    context: WorkspaceRequestContext,
):
    return _workspace_context.set(context)


def reset_workspace_context(token) -> None:
    _workspace_context.reset(token)


def get_workspace_context() -> WorkspaceRequestContext | None:
    return _workspace_context.get()


def require_workspace_context() -> WorkspaceRequestContext:
    context = get_workspace_context()

    if context is None or not context.workspace_id:
        raise RuntimeError("Workspace context is not available.")

    return context
