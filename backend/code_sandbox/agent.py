from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Iterable
import uuid

from sqlalchemy import select
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.code_sandbox.models import CodeSandboxAgentRunModel, utc_now
from backend.code_sandbox.schemas import (
    CodeAgentOperationAudit,
    CodeAgentPlan,
    CodeAgentRun,
    CodeAgentRunRequest,
    CodeAgentRunResponse,
    CodeSandboxInspectResponse,
    CodeSandboxVerifyRequest,
)
from backend.code_sandbox.service import (
    BLOCKED_PATTERNS,
    CodeSandboxConflictError,
    CodeSandboxError,
    CodeSandboxService,
)
from backend.core.events import Event, EventBus
from backend.council.models import CouncilRunModel
from backend.database.models import ModelConfigModel
from backend.gateway.service import AIGateway


class CodeAgentError(CodeSandboxError):
    pass


class CodeAgentResponseError(CodeAgentError):
    pass


class CodeAgentPathError(CodeAgentError):
    pass


class CodeAgentService:
    """Capability-limited coding agent for a Code Sandbox worktree.

    The LLM receives text context and may only request structured write/delete
    operations for exact paths explicitly granted by the caller. It never gets
    a shell, Session, credentials, arbitrary filesystem handles or network tools.
    """

    MAX_CONTEXT_FILE_BYTES = 128 * 1024
    MAX_CONTEXT_TOTAL_BYTES = 768 * 1024
    MAX_WRITE_FILE_BYTES = 512 * 1024
    MAX_WRITE_TOTAL_BYTES = 2 * 1024 * 1024
    MAX_OPERATIONS = 16
    TASK_PREVIEW_LIMIT = 600
    AGENT_BLOCKED_PATTERNS = (
        *BLOCKED_PATTERNS,
        ".gitattributes",
        "**/.gitattributes",
        ".lfsconfig",
        "**/.lfsconfig",
    )

    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        gateway: AIGateway,
        sandbox: CodeSandboxService,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._gateway = gateway
        self._sandbox = sandbox

    async def run(
        self,
        session_id: str,
        workspace_id: str | None,
        request: CodeAgentRunRequest,
    ) -> CodeAgentRunResponse:
        sandbox = self._sandbox.get(session_id, workspace_id)
        if sandbox is None:
            raise CodeAgentError("Code Sandbox session не найден.")
        if sandbox.status in {"applied", "closed"}:
            raise CodeSandboxConflictError(
                f"Sandbox уже находится в состоянии {sandbox.status}."
            )

        task = self._build_task(request, workspace_id)
        if not task.strip():
            raise CodeAgentError(
                "Укажите task или council_run_id с сохранённым успешным Council run."
            )

        worktree = Path(sandbox.worktree_path).resolve()
        if not worktree.is_dir():
            raise CodeSandboxConflictError("Worktree отсутствует на диске.")

        writable = self._normalize_authorized_paths(
            worktree, request.writable_paths, allow_missing=True, purpose="writable"
        )
        if not writable:
            raise CodeAgentPathError("Writable path allowlist не может быть пустым.")
        context_requested = list(dict.fromkeys([*request.context_paths, *request.writable_paths]))
        context = self._normalize_authorized_paths(
            worktree, context_requested, allow_missing=True, purpose="context"
        )
        snapshots = self._snapshot_paths(worktree, writable)
        context_text, actual_context_paths = self._load_context(worktree, context)

        now = utc_now()
        row = CodeSandboxAgentRunModel(
            id=f"code_agent_{uuid.uuid4().hex}",
            session_id=session_id,
            workspace_id=workspace_id,
            council_run_id=request.council_run_id,
            status="running",
            provider=request.provider,
            model=request.model,
            task_sha256=hashlib.sha256(task.encode("utf-8")).hexdigest(),
            task_preview=self._task_preview(request),
            context_paths_json=actual_context_paths,
            writable_paths_json=writable,
            operations_json=[],
            summary="",
            error_message="",
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        await self._publish(
            "code_sandbox.agent.started",
            row,
            {
                "context_paths": actual_context_paths,
                "writable_paths": writable,
                "council_run_id": request.council_run_id,
            },
        )

        inspect_response: CodeSandboxInspectResponse | None = None
        try:
            response = await self._gateway.ask(
                user_prompt=self._build_user_prompt(
                    task=task,
                    context_text=context_text,
                    writable_paths=writable,
                ),
                system_prompt=self._system_prompt(),
                provider=request.provider,
                model=request.model,
                mode="code",
                source="code_sandbox_agent",
                correlation_id=row.id,
                workspace_id=workspace_id,
                actor_id="code-sandbox-agent",
            )
            row.provider = response.provider
            row.model = response.model
            row.gateway_request_id = response.request_id
            row.input_tokens = response.usage.input_tokens
            row.output_tokens = response.usage.output_tokens
            row.total_tokens = response.usage.total_tokens
            calculated_cost = self._response_cost(response.provider, response.model, response.usage.input_tokens, response.usage.output_tokens)
            row.actual_cost_usd = response.cost if response.cost is not None else calculated_cost
            row.cost_status = "known" if row.actual_cost_usd is not None else "unknown"
            if response.status != "success":
                message = response.error.message if response.error else "AI Gateway request failed."
                raise CodeAgentError(message)

            plan = self._parse_plan(response.content)
            self._validate_plan(plan, writable)
            self._assert_snapshot_unchanged(worktree, snapshots)
            audits = self._apply_plan(worktree, plan, writable)
            row.summary = plan.summary
            row.operations_json = [item.model_dump(mode="json") for item in audits]
            row.updated_at = utc_now()
            self._session.flush()

            inspect_response = await self._sandbox.inspect(session_id, workspace_id)
            sandbox_after = inspect_response.session
            if not sandbox_after.files_changed:
                raise CodeAgentResponseError("Агент не создал фактических изменений в worktree.")
            if sandbox_after.blocked_paths:
                row.status = "blocked"
                row.error_message = (
                    "Агент затронул заблокированные пути: "
                    + ", ".join(sandbox_after.blocked_paths)
                )
            elif request.auto_verify:
                verified = await self._sandbox.verify(
                    session_id,
                    workspace_id,
                    CodeSandboxVerifyRequest(
                        profiles=request.verification_profiles,
                        timeout_seconds=request.verification_timeout_seconds,
                    ),
                )
                sandbox_after = verified
                row.status = "completed" if verified.verification_status == "passed" else "failed"
                if row.status == "failed":
                    row.error_message = "Автоматический verification завершился ошибкой."
            else:
                row.status = "completed"

            row.finished_at = utc_now()
            row.updated_at = row.finished_at
            self._session.flush()
            await self._publish(
                "code_sandbox.agent.completed" if row.status == "completed" else "code_sandbox.agent.blocked" if row.status == "blocked" else "code_sandbox.agent.failed",
                row,
                {
                    "operation_count": len(audits),
                    "risk_level": sandbox_after.risk_level,
                    "verification_status": sandbox_after.verification_status,
                },
            )
            return CodeAgentRunResponse(
                run=self._to_schema(row),
                sandbox=sandbox_after,
                inspect=inspect_response,
            )
        except Exception as exc:
            if row.status == "running":
                row.status = "failed"
                row.error_message = str(exc)[:4000]
                row.finished_at = utc_now()
                row.updated_at = row.finished_at
                self._session.flush()
                await self._publish(
                    "code_sandbox.agent.failed",
                    row,
                    {"message": row.error_message},
                )
            if isinstance(exc, CodeSandboxError):
                raise
            raise CodeAgentError(str(exc)) from exc

    def get(
        self,
        run_id: str,
        workspace_id: str | None,
    ) -> CodeAgentRun | None:
        row = self._get_row(run_id, workspace_id)
        return self._to_schema(row) if row else None

    def list_for_session(
        self,
        session_id: str,
        workspace_id: str | None,
        limit: int = 50,
    ) -> list[CodeAgentRun]:
        stmt = (
            select(CodeSandboxAgentRunModel)
            .where(CodeSandboxAgentRunModel.session_id == session_id)
            .order_by(CodeSandboxAgentRunModel.created_at.desc())
            .limit(limit)
        )
        if workspace_id is None:
            stmt = stmt.where(CodeSandboxAgentRunModel.workspace_id.is_(None))
        else:
            stmt = stmt.where(CodeSandboxAgentRunModel.workspace_id == workspace_id)
        return [self._to_schema(row) for row in self._session.scalars(stmt).all()]

    def _build_task(self, request: CodeAgentRunRequest, workspace_id: str | None) -> str:
        parts: list[str] = []
        if request.task:
            parts.append("USER IMPLEMENTATION GOAL:\n" + request.task.strip())
        if request.council_run_id:
            stmt = select(CouncilRunModel).where(CouncilRunModel.id == request.council_run_id)
            if workspace_id is None:
                stmt = stmt.where(CouncilRunModel.workspace_id.is_(None))
            else:
                stmt = stmt.where(CouncilRunModel.workspace_id == workspace_id)
            council = self._session.scalar(stmt)
            if council is None:
                raise CodeAgentError("Council run не найден в текущем Workspace.")
            if council.status not in {"completed", "partial"} or not council.final_answer.strip():
                raise CodeAgentError("Council run не содержит пригодного финального решения.")
            parts.append(
                "COUNCIL DECISION TO IMPLEMENT:\n"
                + council.final_answer.strip()
            )
        return "\n\n".join(parts)

    def _response_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        row = self._session.scalar(
            select(ModelConfigModel).where(
                ModelConfigModel.provider == provider,
                ModelConfigModel.slug == model,
            )
        )
        if row is None:
            return None
        metadata = dict(row.metadata_json or {})
        if metadata.get("billing") == "free":
            return 0.0
        pricing = metadata.get("pricing") if isinstance(metadata.get("pricing"), dict) else metadata
        valid_until = pricing.get("valid_until")
        if valid_until:
            try:
                expires = datetime.fromisoformat(str(valid_until)).date()
            except ValueError:
                return None
            if datetime.now(timezone.utc).date() > expires:
                return None
        try:
            input_price = float(pricing.get("input_per_million_usd"))
            output_price = float(pricing.get("output_per_million_usd"))
        except (TypeError, ValueError):
            return None
        if input_price < 0 or output_price < 0:
            return None
        return round(
            input_tokens * input_price / 1_000_000
            + output_tokens * output_price / 1_000_000,
            10,
        )

    @classmethod
    def _normalize_authorized_paths(
        cls,
        worktree: Path,
        paths: Iterable[str],
        *,
        allow_missing: bool,
        purpose: str,
    ) -> list[str]:
        normalized: list[str] = []
        for raw in paths:
            path = cls._normalize_relative_path(raw)
            if CodeSandboxService._matches(path, cls.AGENT_BLOCKED_PATTERNS):
                raise CodeAgentPathError(f"{purpose} path запрещён политикой Sandbox: {path}")
            target = worktree / Path(*PurePosixPath(path).parts)
            cls._ensure_no_symlink_path(worktree, target)
            if target.exists() and not target.is_file():
                raise CodeAgentPathError(f"{purpose} path должен быть обычным файлом: {path}")
            if not allow_missing and not target.is_file():
                raise CodeAgentPathError(f"{purpose} file не найден: {path}")
            if path not in normalized:
                normalized.append(path)
        return normalized

    @staticmethod
    def _normalize_relative_path(raw: str) -> str:
        value = raw.strip().replace("\\", "/")
        if value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise CodeAgentPathError(f"Недопустимый относительный путь: {raw}")
        normalized = path.as_posix()
        if normalized.startswith(".git/") or normalized == ".git":
            raise CodeAgentPathError("Доступ к .git запрещён.")
        return normalized

    @staticmethod
    def _ensure_no_symlink_path(worktree: Path, target: Path) -> None:
        try:
            relative = target.relative_to(worktree)
        except ValueError as exc:
            raise CodeAgentPathError("Путь выходит за пределы worktree.") from exc
        current = worktree
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise CodeAgentPathError(
                    f"Symlink path запрещён: {relative.as_posix()}"
                )

    def _load_context(self, worktree: Path, paths: list[str]) -> tuple[str, list[str]]:
        blocks: list[str] = []
        actual: list[str] = []
        total = 0
        for relative in paths:
            target = worktree / Path(*PurePosixPath(relative).parts)
            if not target.exists():
                if relative in paths:
                    blocks.append(f"===== FILE: {relative} (MISSING) =====\n")
                    actual.append(relative)
                continue
            data = target.read_bytes()
            if len(data) > self.MAX_CONTEXT_FILE_BYTES:
                raise CodeAgentPathError(
                    f"Context file слишком большой ({len(data)} bytes): {relative}"
                )
            if b"\x00" in data:
                raise CodeAgentPathError(f"Binary context file запрещён: {relative}")
            total += len(data)
            if total > self.MAX_CONTEXT_TOTAL_BYTES:
                raise CodeAgentPathError("Суммарный context превышает безопасный лимит.")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CodeAgentPathError(
                    f"Context file должен быть UTF-8 text: {relative}"
                ) from exc
            blocks.append(f"===== FILE: {relative} =====\n{text}\n")
            actual.append(relative)
        return "\n".join(blocks), actual

    @staticmethod
    def _snapshot_paths(worktree: Path, paths: list[str]) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for relative in paths:
            target = worktree / Path(*PurePosixPath(relative).parts)
            if not target.exists():
                snapshot[relative] = "missing"
            else:
                snapshot[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        return snapshot

    @staticmethod
    def _assert_snapshot_unchanged(worktree: Path, snapshot: dict[str, str]) -> None:
        current = CodeAgentService._snapshot_paths(worktree, list(snapshot))
        if current != snapshot:
            raise CodeSandboxConflictError(
                "Writable files изменились во время работы агента. Запустите agent run повторно."
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a constrained code-editing agent inside an isolated Git worktree. "
            "You have NO shell, network, secret access, or permission to edit arbitrary files. "
            "Return exactly one JSON object and no markdown. Schema: "
            '{"summary":"short summary","operations":[{"action":"write|replace|delete","path":"relative/path","content":"full file for write","old_text":"exact existing text for replace","new_text":"replacement text","reason":"why"}]}. '
            "Prefer replace for localized edits. For replace, old_text MUST match exactly once. "
            "Use write for new files or when full-file replacement is truly required. "
            "Use only paths listed as WRITABLE. Do not mention or request commands. "
            "Prefer the smallest correct change. Never introduce credentials or secrets."
        )

    @staticmethod
    def _build_user_prompt(*, task: str, context_text: str, writable_paths: list[str]) -> str:
        writable = "\n".join(f"- {path}" for path in writable_paths)
        return (
            f"TASK:\n{task}\n\n"
            f"WRITABLE PATHS (exact allowlist):\n{writable}\n\n"
            "CURRENT FILE CONTEXT:\n"
            f"{context_text}\n"
            "Produce the JSON edit plan now."
        )

    @staticmethod
    def _parse_plan(content: str) -> CodeAgentPlan:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CodeAgentResponseError(
                "Coding agent вернул невалидный JSON; изменения не применялись."
            ) from exc
        try:
            return CodeAgentPlan.model_validate(payload)
        except Exception as exc:
            raise CodeAgentResponseError(
                f"Coding agent response не соответствует безопасной схеме: {exc}"
            ) from exc

    def _validate_plan(self, plan: CodeAgentPlan, writable_paths: list[str]) -> None:
        allowed = set(writable_paths)
        seen: set[str] = set()
        total = 0
        if len(plan.operations) > self.MAX_OPERATIONS:
            raise CodeAgentResponseError("Слишком много файловых операций.")
        for operation in plan.operations:
            path = self._normalize_relative_path(operation.path)
            if path not in allowed:
                raise CodeAgentResponseError(
                    f"Агент попытался изменить путь вне allowlist: {path}"
                )
            if path in seen:
                raise CodeAgentResponseError(
                    f"Один путь указан несколько раз: {path}"
                )
            seen.add(path)
            if operation.action == "write":
                if operation.content is None:
                    raise CodeAgentResponseError(
                        f"write operation не содержит content: {path}"
                    )
                if operation.old_text is not None or operation.new_text is not None:
                    raise CodeAgentResponseError(
                        f"write operation не должна содержать old_text/new_text: {path}"
                    )
                size = len(operation.content.encode("utf-8"))
                if size > self.MAX_WRITE_FILE_BYTES:
                    raise CodeAgentResponseError(
                        f"Слишком большой generated file: {path}"
                    )
                total += size
            elif operation.action == "replace":
                if not operation.old_text:
                    raise CodeAgentResponseError(
                        f"replace operation требует непустой old_text: {path}"
                    )
                if operation.new_text is None:
                    raise CodeAgentResponseError(
                        f"replace operation требует new_text: {path}"
                    )
                if operation.content is not None:
                    raise CodeAgentResponseError(
                        f"replace operation не должна содержать content: {path}"
                    )
                total += len(operation.new_text.encode("utf-8"))
            elif operation.content not in {None, ""} or operation.old_text is not None or operation.new_text is not None:
                raise CodeAgentResponseError(
                    f"delete operation не должна содержать content/old_text/new_text: {path}"
                )
        if total > self.MAX_WRITE_TOTAL_BYTES:
            raise CodeAgentResponseError("Суммарный объём generated files превышает лимит.")

    def _apply_plan(
        self,
        worktree: Path,
        plan: CodeAgentPlan,
        writable_paths: list[str],
    ) -> list[CodeAgentOperationAudit]:
        allowed = set(writable_paths)
        audits: list[CodeAgentOperationAudit] = []
        for operation in plan.operations:
            relative = self._normalize_relative_path(operation.path)
            if relative not in allowed:
                raise CodeAgentResponseError(
                    f"Path вне allowlist: {relative}"
                )
            target = worktree / Path(*PurePosixPath(relative).parts)
            self._ensure_no_symlink_path(worktree, target)
            if operation.action == "replace":
                if not target.is_file() or target.is_symlink():
                    raise CodeAgentPathError(
                        f"Replace разрешён только для существующего обычного файла: {relative}"
                    )
                try:
                    current_text = target.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise CodeAgentPathError(
                        f"Replace поддерживает только UTF-8 text: {relative}"
                    ) from exc
                old_text = operation.old_text or ""
                count = current_text.count(old_text)
                if count != 1:
                    raise CodeAgentResponseError(
                        f"replace old_text должен совпадать ровно один раз ({count}): {relative}"
                    )
                new_content = current_text.replace(old_text, operation.new_text or "", 1)
                self._atomic_write_text(target, new_content)
                audits.append(
                    CodeAgentOperationAudit(
                        action="replace",
                        path=relative,
                        bytes=len(new_content.encode("utf-8")),
                        reason=operation.reason,
                    )
                )
                continue

            if operation.action == "delete":
                if target.exists():
                    if not target.is_file() or target.is_symlink():
                        raise CodeAgentPathError(
                            f"Delete разрешён только для обычного файла: {relative}"
                        )
                    target.unlink()
                audits.append(
                    CodeAgentOperationAudit(
                        action="delete", path=relative, bytes=0, reason=operation.reason
                    )
                )
                continue

            content = operation.content or ""
            encoded = content.encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_no_symlink_path(worktree, target)
            self._atomic_write_text(target, content)
            audits.append(
                CodeAgentOperationAudit(
                    action="write",
                    path=relative,
                    bytes=len(encoded),
                    reason=operation.reason,
                )
            )
        return audits

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        encoded = content.encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=".ai-studio-agent-", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @classmethod
    def _task_preview(cls, request: CodeAgentRunRequest) -> str:
        if request.task:
            return request.task.strip()[: cls.TASK_PREVIEW_LIMIT]
        if request.council_run_id:
            return f"Council handoff: {request.council_run_id}"
        return ""

    def _get_row(
        self, run_id: str, workspace_id: str | None
    ) -> CodeSandboxAgentRunModel | None:
        stmt = select(CodeSandboxAgentRunModel).where(CodeSandboxAgentRunModel.id == run_id)
        if workspace_id is None:
            stmt = stmt.where(CodeSandboxAgentRunModel.workspace_id.is_(None))
        else:
            stmt = stmt.where(CodeSandboxAgentRunModel.workspace_id == workspace_id)
        return self._session.scalar(stmt)

    @staticmethod
    def _to_schema(row: CodeSandboxAgentRunModel) -> CodeAgentRun:
        return CodeAgentRun(
            id=row.id,
            session_id=row.session_id,
            workspace_id=row.workspace_id,
            council_run_id=row.council_run_id,
            status=row.status,
            provider=row.provider,
            model=row.model,
            gateway_request_id=row.gateway_request_id,
            task_sha256=row.task_sha256,
            task_preview=row.task_preview,
            context_paths=list(row.context_paths_json or []),
            writable_paths=list(row.writable_paths_json or []),
            operations=[CodeAgentOperationAudit.model_validate(item) for item in (row.operations_json or [])],
            summary=row.summary,
            error_message=row.error_message,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            actual_cost_usd=row.actual_cost_usd,
            cost_status=row.cost_status,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            updated_at=row.updated_at,
        )

    async def _publish(
        self,
        event_type: str,
        row: CodeSandboxAgentRunModel,
        payload: dict,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="code_sandbox_agent",
                correlation_id=row.id,
                payload={
                    "run_id": row.id,
                    "session_id": row.session_id,
                    "workspace_id": row.workspace_id,
                    "status": row.status,
                    **payload,
                },
            )
        )
