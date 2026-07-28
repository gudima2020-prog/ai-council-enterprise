from __future__ import annotations

import pytest

from backend.orchestration.tool_runtime import ToolExecutionContext, ToolExecutionError


class FakeAccessor:
    async def get(self, alias: str) -> str:
        return f"secret:{alias}"


@pytest.mark.asyncio
async def test_tool_context_resolves_secret_only_through_accessor() -> None:
    context = ToolExecutionContext(
        invocation_id="inv",
        tool_id="tool",
        tool_key="test",
        handler_ref="test.handler",
        workspace_id=None,
        agent_id=None,
        plan_id=None,
        step_id=None,
        correlation_id=None,
        input={"safe": True},
        isolation_mode="restricted",
        capabilities={"secrets": True},
        metadata={"secret_aliases": ["api_key"]},
        secret_accessor=FakeAccessor(),  # type: ignore[arg-type]
    )
    assert await context.secret("api_key") == "secret:api_key"
    assert "secret:" not in repr(context.input)


@pytest.mark.asyncio
async def test_tool_context_without_accessor_cannot_request_secret() -> None:
    context = ToolExecutionContext(
        invocation_id="inv",
        tool_id="tool",
        tool_key="test",
        handler_ref="test.handler",
        workspace_id=None,
        agent_id=None,
        plan_id=None,
        step_id=None,
        correlation_id=None,
        input={},
        isolation_mode="restricted",
        capabilities={"secrets": False},
        metadata={},
    )
    with pytest.raises(ToolExecutionError):
        await context.secret("api_key")
