from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.orchestration.models import (
    AgentProfileModel,
    ToolDefinitionModel,
    ToolInvocationModel,
    ToolPermissionModel,
)
from backend.orchestration.tool_schemas import (
    ToolCreate,
    ToolPermissionCreate,
    ToolUpdate,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolRegistryError(ValueError):
    pass


class ToolNotFound(ToolRegistryError):
    pass


class ToolPermissionDenied(ToolRegistryError):
    pass


class ToolRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, request: ToolCreate) -> ToolDefinitionModel:
        row = ToolDefinitionModel(
            workspace_id=request.workspace_id,
            tool_key=request.tool_key,
            display_name=request.display_name,
            description=request.description,
            kind=request.kind,
            handler_ref=request.handler_ref,
            risk_level=request.risk_level,
            enabled=request.enabled,
            requires_explicit_allow=request.requires_explicit_allow,
            isolation_mode=request.isolation_mode,
            timeout_seconds=request.timeout_seconds,
            max_concurrency=request.max_concurrency,
            max_input_bytes=request.max_input_bytes,
            max_output_bytes=request.max_output_bytes,
            allow_network=request.allow_network,
            allow_filesystem_read=request.allow_filesystem_read,
            allow_filesystem_write=request.allow_filesystem_write,
            input_schema_json=request.input_schema,
            output_schema_json=request.output_schema,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, tool_id: str) -> ToolDefinitionModel | None:
        return self.session.get(ToolDefinitionModel, tool_id)

    def get_by_key_exact(
        self,
        tool_key: str,
        workspace_id: str | None,
    ) -> ToolDefinitionModel | None:
        statement = select(ToolDefinitionModel).where(
            ToolDefinitionModel.tool_key == tool_key,
            ToolDefinitionModel.workspace_id == workspace_id,
        )
        return self.session.scalar(statement)

    def resolve(
        self,
        tool_key: str,
        workspace_id: str | None,
    ) -> ToolDefinitionModel | None:
        statement = select(ToolDefinitionModel).where(
            ToolDefinitionModel.tool_key == tool_key,
        )

        if workspace_id is None:
            statement = statement.where(
                ToolDefinitionModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    ToolDefinitionModel.workspace_id == workspace_id,
                    ToolDefinitionModel.workspace_id.is_(None),
                )
            ).order_by(
                case(
                    (ToolDefinitionModel.workspace_id == workspace_id, 0),
                    else_=1,
                )
            )

        return self.session.scalar(statement.limit(1))

    def list(
        self,
        *,
        workspace_id: str | None = None,
        include_global: bool = True,
        enabled: bool | None = None,
        risk_level: str | None = None,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolDefinitionModel]:
        statement = select(ToolDefinitionModel)

        if workspace_id is not None:
            if include_global:
                statement = statement.where(
                    or_(
                        ToolDefinitionModel.workspace_id == workspace_id,
                        ToolDefinitionModel.workspace_id.is_(None),
                    )
                )
            else:
                statement = statement.where(
                    ToolDefinitionModel.workspace_id == workspace_id
                )
        if enabled is not None:
            statement = statement.where(
                ToolDefinitionModel.enabled == enabled
            )
        if risk_level is not None:
            statement = statement.where(
                ToolDefinitionModel.risk_level == risk_level
            )
        if kind is not None:
            statement = statement.where(ToolDefinitionModel.kind == kind)

        statement = (
            statement
            .order_by(
                ToolDefinitionModel.tool_key.asc(),
                ToolDefinitionModel.workspace_id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def update(
        self,
        row: ToolDefinitionModel,
        request: ToolUpdate,
    ) -> ToolDefinitionModel:
        values = request.model_dump(exclude_unset=True)
        mapping = {
            "display_name": "display_name",
            "description": "description",
            "handler_ref": "handler_ref",
            "risk_level": "risk_level",
            "enabled": "enabled",
            "requires_explicit_allow": "requires_explicit_allow",
            "isolation_mode": "isolation_mode",
            "timeout_seconds": "timeout_seconds",
            "max_concurrency": "max_concurrency",
            "max_input_bytes": "max_input_bytes",
            "max_output_bytes": "max_output_bytes",
            "allow_network": "allow_network",
            "allow_filesystem_read": "allow_filesystem_read",
            "allow_filesystem_write": "allow_filesystem_write",
            "input_schema": "input_schema_json",
            "output_schema": "output_schema_json",
            "metadata": "metadata_json",
        }
        for source, target in mapping.items():
            if source in values:
                setattr(row, target, values[source])
        self.session.flush()
        return row

    def delete(self, row: ToolDefinitionModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def has_invocations(self, tool_id: str) -> bool:
        statement = select(func.count(ToolInvocationModel.id)).where(
            ToolInvocationModel.tool_id == tool_id
        )
        return bool(self.session.scalar(statement))

    def add_permission(
        self,
        tool_id: str,
        request: ToolPermissionCreate,
    ) -> ToolPermissionModel:
        row = ToolPermissionModel(
            tool_id=tool_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            effect=request.effect,
            action=request.action,
            constraints_json=request.constraints,
            expires_at=request.expires_at,
            created_by=request.created_by,
            reason=request.reason,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_permission(
        self,
        tool_id: str,
        permission_id: str,
    ) -> ToolPermissionModel | None:
        statement = select(ToolPermissionModel).where(
            ToolPermissionModel.id == permission_id,
            ToolPermissionModel.tool_id == tool_id,
        )
        return self.session.scalar(statement)

    def delete_permission(self, row: ToolPermissionModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def list_permissions(
        self,
        tool_id: str,
    ) -> list[ToolPermissionModel]:
        statement = (
            select(ToolPermissionModel)
            .where(ToolPermissionModel.tool_id == tool_id)
            .order_by(ToolPermissionModel.created_at.asc())
        )
        return list(self.session.scalars(statement).all())

    def applicable_permissions(
        self,
        *,
        tool_id: str,
        workspace_id: str | None,
        agent_id: str | None,
    ) -> list[ToolPermissionModel]:
        now = utc_now()
        statement = select(ToolPermissionModel).where(
            ToolPermissionModel.tool_id == tool_id,
            ToolPermissionModel.action == "execute",
            or_(
                ToolPermissionModel.expires_at.is_(None),
                ToolPermissionModel.expires_at > now,
            ),
        )

        if workspace_id is None:
            statement = statement.where(
                ToolPermissionModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    ToolPermissionModel.workspace_id.is_(None),
                    ToolPermissionModel.workspace_id == workspace_id,
                )
            )

        if agent_id is None:
            statement = statement.where(
                ToolPermissionModel.agent_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    ToolPermissionModel.agent_id.is_(None),
                    ToolPermissionModel.agent_id == agent_id,
                )
            )

        statement = statement.order_by(ToolPermissionModel.created_at.asc())
        return list(self.session.scalars(statement).all())

    def create_invocation(
        self,
        *,
        tool: ToolDefinitionModel | None,
        tool_key: str,
        workspace_id: str | None,
        agent_id: str | None,
        plan_id: str | None,
        step_id: str | None,
        correlation_id: str | None,
        status: str,
        isolation_mode: str,
        input_data: dict[str, Any],
        policy: dict[str, Any],
        error: str | None = None,
    ) -> ToolInvocationModel:
        row = ToolInvocationModel(
            tool_id=tool.id if tool else None,
            tool_key=tool_key,
            workspace_id=workspace_id,
            agent_id=agent_id,
            plan_id=plan_id,
            step_id=step_id,
            correlation_id=correlation_id,
            status=status,
            isolation_mode=isolation_mode,
            input_json=input_data,
            policy_json=policy,
            error=error,
            finished_at=utc_now() if status == "denied" else None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_invocation(
        self,
        invocation_id: str,
    ) -> ToolInvocationModel | None:
        return self.session.get(ToolInvocationModel, invocation_id)

    def finish_invocation(
        self,
        row: ToolInvocationModel,
        *,
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
        duration_ms: float,
    ) -> ToolInvocationModel:
        row.status = status
        row.output_json = output
        row.error = error
        row.duration_ms = duration_ms
        row.finished_at = utc_now()
        self.session.flush()
        return row

    def list_invocations(
        self,
        *,
        tool_id: str | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
        plan_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolInvocationModel]:
        statement = select(ToolInvocationModel)
        if tool_id is not None:
            statement = statement.where(
                ToolInvocationModel.tool_id == tool_id
            )
        if workspace_id is not None:
            statement = statement.where(
                ToolInvocationModel.workspace_id == workspace_id
            )
        if agent_id is not None:
            statement = statement.where(
                ToolInvocationModel.agent_id == agent_id
            )
        if plan_id is not None:
            statement = statement.where(
                ToolInvocationModel.plan_id == plan_id
            )
        if status is not None:
            statement = statement.where(
                ToolInvocationModel.status == status
            )
        statement = (
            statement
            .order_by(ToolInvocationModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())


class ToolRegistryService:
    DEFAULT_TOOLS = (
        ToolCreate(
            tool_key="echo",
            display_name="Echo",
            description="Returns the validated input object.",
            kind="builtin",
            handler_ref="builtin.echo",
            risk_level="low",
            max_concurrency=20,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        ToolCreate(
            tool_key="merge",
            display_name="Merge objects",
            description="Merges objects from input.items in order.",
            kind="builtin",
            handler_ref="builtin.merge",
            risk_level="low",
            max_concurrency=20,
            input_schema={
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
            },
            output_schema={"type": "object"},
        ),
        ToolCreate(
            tool_key="select",
            display_name="Select JSON path",
            description="Selects a value from input.data using input.path.",
            kind="builtin",
            handler_ref="builtin.select",
            risk_level="low",
            max_concurrency=20,
            input_schema={
                "type": "object",
                "required": ["path", "data"],
                "properties": {
                    "path": {"type": "string", "minLength": 1}
                },
            },
            output_schema={"type": "object", "required": ["value"]},
        ),
        ToolCreate(
            tool_key="sleep",
            display_name="Controlled sleep",
            description="Waits for a bounded number of seconds.",
            kind="builtin",
            handler_ref="builtin.sleep",
            risk_level="low",
            timeout_seconds=3600,
            max_concurrency=20,
            input_schema={
                "type": "object",
                "required": ["seconds"],
                "properties": {
                    "seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 3600,
                    }
                },
            },
            output_schema={
                "type": "object",
                "required": ["slept_seconds"],
            },
        ),
    )

    def __init__(
        self,
        repository: ToolRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus

    def seed_defaults(self) -> int:
        created = 0
        for request in self.DEFAULT_TOOLS:
            if self._repository.get_by_key_exact(
                request.tool_key,
                None,
            ) is not None:
                continue
            self._repository.create(request)
            created += 1
        return created

    async def create(self, request: ToolCreate) -> dict[str, Any]:
        if self._repository.get_by_key_exact(
            request.tool_key,
            request.workspace_id,
        ) is not None:
            raise ToolRegistryError(
                f"Tool с ключом {request.tool_key} уже существует."
            )
        try:
            row = self._repository.create(request)
        except IntegrityError as exc:
            raise ToolRegistryError(
                f"Tool с ключом {request.tool_key} уже существует."
            ) from exc

        await self._publish("tool.created", row)
        return self.serialize_tool(row)

    def get(self, tool_id: str) -> dict[str, Any] | None:
        row = self._repository.get(tool_id)
        return None if row is None else self.serialize_tool(row, full=True)

    def list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            self.serialize_tool(row)
            for row in self._repository.list(**kwargs)
        ]

    async def update(
        self,
        tool_id: str,
        request: ToolUpdate,
    ) -> dict[str, Any] | None:
        row = self._repository.get(tool_id)
        if row is None:
            return None
        self._repository.update(row, request)
        await self._publish("tool.updated", row)
        return self.serialize_tool(row, full=True)

    async def delete(self, tool_id: str) -> bool:
        row = self._repository.get(tool_id)
        if row is None:
            return False
        if self._repository.has_invocations(tool_id):
            raise ToolRegistryError(
                "Tool с историей запусков нельзя удалить; отключите его."
            )
        workspace_id = row.workspace_id
        tool_key = row.tool_key
        self._repository.delete(row)
        await self._event_bus.publish(
            Event(
                event_type="tool.deleted",
                source="tool_registry_service",
                workspace_id=workspace_id,
                payload={"tool_id": tool_id, "tool_key": tool_key},
            )
        )
        return True

    async def add_permission(
        self,
        tool_id: str,
        request: ToolPermissionCreate,
    ) -> dict[str, Any] | None:
        tool = self._repository.get(tool_id)
        if tool is None:
            return None
        if request.agent_id is not None:
            agent = self._repository.session.get(
                AgentProfileModel,
                request.agent_id,
            )
            if agent is None:
                raise ToolRegistryError("Agent не найден.")
            if (
                request.workspace_id is not None
                and agent.workspace_id not in {None, request.workspace_id}
            ):
                raise ToolRegistryError(
                    "Agent не принадлежит указанному Workspace."
                )
        row = self._repository.add_permission(tool_id, request)
        await self._event_bus.publish(
            Event(
                event_type="tool.permission.created",
                source="tool_registry_service",
                workspace_id=request.workspace_id or tool.workspace_id,
                payload={
                    "tool_id": tool.id,
                    "tool_key": tool.tool_key,
                    "permission_id": row.id,
                    "effect": row.effect,
                    "agent_id": row.agent_id,
                },
            )
        )
        return self.serialize_permission(row)

    def list_permissions(
        self,
        tool_id: str,
    ) -> list[dict[str, Any]] | None:
        if self._repository.get(tool_id) is None:
            return None
        return [
            self.serialize_permission(row)
            for row in self._repository.list_permissions(tool_id)
        ]

    async def delete_permission(
        self,
        tool_id: str,
        permission_id: str,
    ) -> bool | None:
        if self._repository.get(tool_id) is None:
            return None
        row = self._repository.get_permission(tool_id, permission_id)
        if row is None:
            return False
        self._repository.delete_permission(row)
        await self._event_bus.publish(
            Event(
                event_type="tool.permission.deleted",
                source="tool_registry_service",
                workspace_id=row.workspace_id,
                payload={
                    "tool_id": tool_id,
                    "permission_id": permission_id,
                },
            )
        )
        return True

    def evaluate(
        self,
        *,
        tool: ToolDefinitionModel,
        workspace_id: str | None,
        agent_id: str | None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        matched = self._repository.applicable_permissions(
            tool_id=tool.id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        denied = [row for row in matched if row.effect == "deny"]
        allowed = [row for row in matched if row.effect == "allow"]

        if not tool.enabled:
            reasons.append("tool_disabled")
        if tool.workspace_id is not None and tool.workspace_id != workspace_id:
            reasons.append("workspace_mismatch")
        if denied:
            reasons.append("explicit_deny")

        if agent_id is not None:
            agent = self._repository.session.get(AgentProfileModel, agent_id)
            if agent is None or not agent.enabled:
                reasons.append("agent_unavailable")
            elif agent.tools_json and tool.tool_key not in set(agent.tools_json):
                reasons.append("tool_not_declared_for_agent")

        explicit_required = (
            tool.requires_explicit_allow
            or tool.risk_level in {"high", "critical"}
            or tool.kind in {"http", "subprocess"}
            or tool.allow_network
            or tool.allow_filesystem_write
        )
        if explicit_required and not allowed:
            reasons.append("explicit_allow_required")

        decision = not reasons
        return {
            "allowed": decision,
            "tool_id": tool.id,
            "tool_key": tool.tool_key,
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "risk_level": tool.risk_level,
            "kind": tool.kind,
            "isolation_mode": tool.isolation_mode,
            "explicit_allow_required": explicit_required,
            "matched_permission_ids": [row.id for row in matched],
            "allow_permission_ids": [row.id for row in allowed],
            "deny_permission_ids": [row.id for row in denied],
            "reasons": reasons,
            "capabilities": {
                "network": tool.allow_network,
                "filesystem_read": tool.allow_filesystem_read,
                "filesystem_write": tool.allow_filesystem_write,
            },
            "input_size_hint": len(str(input_data or {})),
        }

    def resolve(
        self,
        tool_key: str,
        workspace_id: str | None,
    ) -> ToolDefinitionModel | None:
        return self._repository.resolve(tool_key.strip().lower(), workspace_id)

    def list_invocations(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            self.serialize_invocation(row)
            for row in self._repository.list_invocations(**kwargs)
        ]

    def get_invocation(
        self,
        invocation_id: str,
    ) -> dict[str, Any] | None:
        row = self._repository.get_invocation(invocation_id)
        return None if row is None else self.serialize_invocation(row)

    async def _publish(
        self,
        event_type: str,
        tool: ToolDefinitionModel,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="tool_registry_service",
                workspace_id=tool.workspace_id,
                payload={
                    "tool_id": tool.id,
                    "tool_key": tool.tool_key,
                    "risk_level": tool.risk_level,
                    "enabled": tool.enabled,
                },
            )
        )

    def serialize_tool(
        self,
        row: ToolDefinitionModel,
        *,
        full: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "tool_key": row.tool_key,
            "display_name": row.display_name,
            "description": row.description,
            "kind": row.kind,
            "handler_ref": row.handler_ref,
            "risk_level": row.risk_level,
            "enabled": row.enabled,
            "requires_explicit_allow": row.requires_explicit_allow,
            "isolation_mode": row.isolation_mode,
            "timeout_seconds": row.timeout_seconds,
            "max_concurrency": row.max_concurrency,
            "max_input_bytes": row.max_input_bytes,
            "max_output_bytes": row.max_output_bytes,
            "allow_network": row.allow_network,
            "allow_filesystem_read": row.allow_filesystem_read,
            "allow_filesystem_write": row.allow_filesystem_write,
            "input_schema": row.input_schema_json,
            "output_schema": row.output_schema_json,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        if full:
            payload["permissions"] = [
                self.serialize_permission(item)
                for item in self._repository.list_permissions(row.id)
            ]
        return payload

    @staticmethod
    def serialize_permission(row: ToolPermissionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "tool_id": row.tool_id,
            "workspace_id": row.workspace_id,
            "agent_id": row.agent_id,
            "effect": row.effect,
            "action": row.action,
            "constraints": row.constraints_json,
            "expires_at": row.expires_at,
            "created_by": row.created_by,
            "reason": row.reason,
            "created_at": row.created_at,
        }

    @staticmethod
    def serialize_invocation(row: ToolInvocationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "tool_id": row.tool_id,
            "tool_key": row.tool_key,
            "workspace_id": row.workspace_id,
            "agent_id": row.agent_id,
            "plan_id": row.plan_id,
            "step_id": row.step_id,
            "correlation_id": row.correlation_id,
            "status": row.status,
            "isolation_mode": row.isolation_mode,
            "input": row.input_json,
            "output": row.output_json,
            "policy": row.policy_json,
            "error": row.error,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at,
        }
