from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from backend.core.events import Event, EventBus
from backend.council.models import (
    CouncilCostLedgerModel,
    CouncilCostReservationModel,
    CouncilRunMemberModel,
    CouncilRunModel,
)
from backend.council.repository import CouncilRunRepository
from backend.council.schemas import (
    CouncilAuxiliaryResult,
    CouncilExecutionMode,
    CouncilMemberRequest,
    CouncilMemberResult,
    CouncilOrchestrationTrace,
    CouncilRetentionPolicy,
    CouncilRetentionPolicyUpdate,
    CouncilRetentionPurgeResponse,
    CouncilRole,
    CouncilRunDeleteResponse,
    CouncilRunHistoryDetail,
    CouncilRunHistoryPage,
    CouncilRunRequest,
    CouncilRunResponse,
    CouncilRunSummary,
    CouncilSynthesis,
    CouncilUsage,
)
from backend.council.service import (
    CouncilCancelledError,
    CouncilExecutionError,
)
from backend.repositories.settings import SettingsRepository
from backend.repositories.models import ModelRepository


RETENTION_SETTING_KEY = "council.history.retention"


@dataclass(frozen=True)
class CouncilRetryPlan:
    request: CouncilRunRequest
    reused_member_results: dict[int, CouncilMemberResult]
    failed_member_indexes: tuple[int, ...]


@dataclass(frozen=True)
class CouncilCostSettlement:
    entries: list[CouncilCostLedgerModel]
    actual_cost_usd: float | None
    actual_cost_status: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class CouncilHistoryService:
    def __init__(
        self,
        *,
        repository: CouncilRunRepository,
        event_bus: EventBus,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._settings_repository = settings_repository
        self._model_repository = ModelRepository(repository.session)

    async def record_success(
        self,
        *,
        request: CouncilRunRequest,
        result: CouncilRunResponse,
        replay_of_run_id: str | None = None,
    ) -> CouncilRunHistoryDetail:
        policy = self.get_retention_policy(request.workspace_id)
        synthesis = result.synthesis
        settlement = self._settlement(
            run_id=result.run_id,
            workspace_id=request.workspace_id,
            members=result.members,
            synthesis=synthesis,
            auxiliary=result.orchestration.auxiliary_calls,
            include_synthesis=result.orchestration.finalizer_stage != "solo",
        )
        run = CouncilRunModel(
            id=result.run_id,
            workspace_id=request.workspace_id,
            replay_of_run_id=replay_of_run_id,
            estimated_cost_usd=request.estimated_cost_usd,
            cost_estimate_status=request.cost_estimate_status,
            cost_approval_id=request.cost_approval_id,
            cost_reservation_id=request.cost_reservation_id,
            actual_cost_usd=settlement.actual_cost_usd,
            actual_cost_status=settlement.actual_cost_status,
            actual_input_tokens=settlement.input_tokens,
            actual_output_tokens=settlement.output_tokens,
            actual_total_tokens=settlement.total_tokens,
            status=result.status,
            execution_mode=request.execution_mode.value,
            question=result.question,
            mode=result.mode,
            synthesizer_provider=request.synthesizer_provider,
            synthesizer_model=request.synthesizer_model,
            synthesis_provider=synthesis.provider,
            synthesis_model=synthesis.model,
            synthesis_requested_model=synthesis.requested_model,
            synthesis_status=synthesis.status,
            synthesis_fallback_used=synthesis.fallback_used,
            synthesis_fallback_model=synthesis.fallback_model,
            final_answer=synthesis.final_answer,
            consensus_json=list(synthesis.consensus),
            disagreements_json=list(synthesis.disagreements),
            recommendations_json=list(synthesis.recommendations),
            confidence=synthesis.confidence,
            member_count=len(result.members),
            successful_member_count=sum(
                member.status == "success" for member in result.members
            ),
            duration_ms=result.duration_ms,
            correlation_id=request.correlation_id,
            actor_id=request.actor_id,
            error_code=None,
            error_message="",
            metadata_json={
                "schema_version": 1,
                "retention_policy": policy.model_dump(mode="json"),
                "member_timeout_seconds": (
                    request.member_timeout_seconds
                ),
                "synthesis_usage": synthesis.usage.model_dump(mode="json"),
                "synthesis_provider_reported_cost_usd": (
                    synthesis.provider_reported_cost_usd
                ),
                "orchestration": result.orchestration.model_dump(mode="json"),
                "orchestration_request": self._orchestration_request_metadata(request),
            },
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        run.cost_entries = settlement.entries
        stored = self._repository.add(
            run,
            self._member_rows(
                result.run_id,
                result.members,
                policy=policy,
            ),
        )
        self._settle_reservation(
            reservation_id=request.cost_reservation_id,
            run_id=result.run_id,
            settlement=settlement,
        )
        if policy.auto_delete_enabled:
            await self._purge_expired(
                workspace_id=request.workspace_id,
                retention_days=policy.retention_days,
            )
        await self._publish_saved(stored)
        return self._detail(stored)

    async def record_failure(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilExecutionError,
        replay_of_run_id: str | None = None,
    ) -> CouncilRunHistoryDetail:
        policy = self.get_retention_policy(request.workspace_id)
        settlement = self._settlement(
            run_id=error.run_id,
            workspace_id=request.workspace_id,
            members=error.member_results,
            synthesis=None,
            auxiliary=error.auxiliary_results,
        )
        run = CouncilRunModel(
            id=error.run_id,
            workspace_id=request.workspace_id,
            replay_of_run_id=replay_of_run_id,
            estimated_cost_usd=request.estimated_cost_usd,
            cost_estimate_status=request.cost_estimate_status,
            cost_approval_id=request.cost_approval_id,
            cost_reservation_id=request.cost_reservation_id,
            actual_cost_usd=settlement.actual_cost_usd,
            actual_cost_status=settlement.actual_cost_status,
            actual_input_tokens=settlement.input_tokens,
            actual_output_tokens=settlement.output_tokens,
            actual_total_tokens=settlement.total_tokens,
            status="failed",
            execution_mode=request.execution_mode.value,
            question=request.question,
            mode=request.mode,
            synthesizer_provider=request.synthesizer_provider,
            synthesizer_model=request.synthesizer_model,
            synthesis_provider=None,
            synthesis_model=None,
            synthesis_requested_model=None,
            synthesis_status=None,
            synthesis_fallback_used=False,
            synthesis_fallback_model=None,
            final_answer="",
            consensus_json=[],
            disagreements_json=[],
            recommendations_json=[],
            confidence=None,
            member_count=len(error.member_results),
            successful_member_count=0,
            duration_ms=error.duration_ms,
            correlation_id=request.correlation_id,
            actor_id=request.actor_id,
            error_code="COUNCIL_ALL_MEMBERS_FAILED",
            error_message=str(error),
            metadata_json={
                "schema_version": 1,
                "retention_policy": policy.model_dump(mode="json"),
                "member_timeout_seconds": (
                    request.member_timeout_seconds
                ),
                "orchestration_request": self._orchestration_request_metadata(request),
            },
            started_at=error.started_at,
            finished_at=error.finished_at,
        )
        run.cost_entries = settlement.entries
        stored = self._repository.add(
            run,
            self._member_rows(
                error.run_id,
                error.member_results,
                policy=policy,
            ),
        )
        self._settle_reservation(
            reservation_id=request.cost_reservation_id,
            run_id=error.run_id,
            settlement=settlement,
        )
        if policy.auto_delete_enabled:
            await self._purge_expired(
                workspace_id=request.workspace_id,
                retention_days=policy.retention_days,
            )
        await self._publish_saved(stored)
        return self._detail(stored)

    async def record_cancelled(
        self,
        *,
        request: CouncilRunRequest,
        error: CouncilCancelledError,
        replay_of_run_id: str | None = None,
    ) -> CouncilRunHistoryDetail:
        policy = self.get_retention_policy(request.workspace_id)
        settlement = self._settlement(
            run_id=error.run_id,
            workspace_id=request.workspace_id,
            members=error.member_results,
            synthesis=None,
            auxiliary=error.auxiliary_results,
        )
        run = CouncilRunModel(
            id=error.run_id,
            workspace_id=request.workspace_id,
            replay_of_run_id=replay_of_run_id,
            estimated_cost_usd=request.estimated_cost_usd,
            cost_estimate_status=request.cost_estimate_status,
            cost_approval_id=request.cost_approval_id,
            cost_reservation_id=request.cost_reservation_id,
            actual_cost_usd=settlement.actual_cost_usd,
            actual_cost_status=settlement.actual_cost_status,
            actual_input_tokens=settlement.input_tokens,
            actual_output_tokens=settlement.output_tokens,
            actual_total_tokens=settlement.total_tokens,
            status="cancelled",
            execution_mode=request.execution_mode.value,
            question=request.question,
            mode=request.mode,
            synthesizer_provider=request.synthesizer_provider,
            synthesizer_model=request.synthesizer_model,
            synthesis_provider=None,
            synthesis_model=None,
            synthesis_requested_model=None,
            synthesis_status=None,
            synthesis_fallback_used=False,
            synthesis_fallback_model=None,
            final_answer="",
            consensus_json=[],
            disagreements_json=[],
            recommendations_json=[],
            confidence=None,
            member_count=len(request.members),
            successful_member_count=sum(
                member.status == "success"
                for member in error.member_results
            ),
            duration_ms=error.duration_ms,
            correlation_id=request.correlation_id,
            actor_id=request.actor_id,
            error_code="COUNCIL_CANCELLED",
            error_message=str(error),
            metadata_json={
                "schema_version": 1,
                "retention_policy": policy.model_dump(mode="json"),
                "member_timeout_seconds": (
                    request.member_timeout_seconds
                ),
                "orchestration_request": self._orchestration_request_metadata(request),
            },
            started_at=error.started_at,
            finished_at=error.finished_at,
        )
        run.cost_entries = settlement.entries
        stored = self._repository.add(
            run,
            self._member_rows(
                error.run_id,
                error.member_results,
                policy=policy,
            ),
        )
        self._settle_reservation(
            reservation_id=request.cost_reservation_id,
            run_id=error.run_id,
            settlement=settlement,
        )
        if policy.auto_delete_enabled:
            await self._purge_expired(
                workspace_id=request.workspace_id,
                retention_days=policy.retention_days,
            )
        await self._publish_saved(stored)
        return self._detail(stored)

    def list_runs(
        self,
        *,
        workspace_id: str | None,
        status: str | None = None,
        mode: str | None = None,
        query: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CouncilRunHistoryPage:
        if (
            started_from is not None
            and started_to is not None
            and started_from > started_to
        ):
            raise ValueError("started_from must not be after started_to.")

        rows = self._repository.list_for_workspace(
            workspace_id=workspace_id,
            status=status,
            mode=mode,
            query=query,
            started_from=started_from,
            started_to=started_to,
            limit=limit,
            offset=offset,
        )
        total = self._repository.count_for_workspace(
            workspace_id=workspace_id,
            status=status,
            mode=mode,
            query=query,
            started_from=started_from,
            started_to=started_to,
        )
        return CouncilRunHistoryPage(
            workspace_id=workspace_id,
            items=[self._summary(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(rows) < total,
        )

    def get_run(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilRunHistoryDetail | None:
        row = self._repository.get_for_workspace(
            run_id=run_id,
            workspace_id=workspace_id,
        )
        return None if row is None else self._detail(row)

    def build_replay_request(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilRunRequest | None:
        row = self._repository.get_for_workspace(
            run_id=run_id,
            workspace_id=workspace_id,
        )
        if row is None:
            return None
        return CouncilRunRequest(
            question=row.question,
            members=[
                CouncilMemberRequest(
                    provider=member.provider,
                    model=member.requested_model or member.model,
                    role=CouncilRole(member.role),
                    label=member.label,
                )
                for member in row.members
            ],
            mode=row.mode,
            execution_mode=CouncilExecutionMode(row.execution_mode or "council"),
            synthesizer_provider=row.synthesizer_provider,
            synthesizer_model=row.synthesizer_model,
            reviewer_provider=self._orchestration_config(row).get("reviewer_provider"),
            reviewer_model=self._orchestration_config(row).get("reviewer_model"),
            arbiter_provider=self._orchestration_config(row).get("arbiter_provider"),
            arbiter_model=self._orchestration_config(row).get("arbiter_model"),
            delegation_max_calls=int(self._orchestration_config(row).get("delegation_max_calls") or 4),
            delegation_max_depth=1,
            workspace_id=workspace_id,
            correlation_id=None,
            actor_id=row.actor_id,
            member_timeout_seconds=self._member_timeout(row),
        )

    def build_failed_retry_plan(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilRetryPlan | None:
        row = self._repository.get_for_workspace(
            run_id=run_id,
            workspace_id=workspace_id,
        )
        if row is None:
            return None

        failed_indexes = tuple(
            member.ordinal
            for member in row.members
            if member.status == "error"
        )
        if not failed_indexes:
            raise ValueError(
                "В сохранённом запуске нет неуспешных участников."
            )

        reused: dict[int, CouncilMemberResult] = {}
        for member in row.members:
            if member.status != "success":
                continue
            result = self._member_result(member)
            if not result.answer.strip():
                raise ValueError(
                    "Нельзя повторить только ошибки: ответы успешных "
                    "участников не были сохранены политикой хранения."
                )
            reused[member.ordinal] = result

        request = CouncilRunRequest(
            question=row.question,
            members=[
                CouncilMemberRequest(
                    provider=member.provider,
                    model=member.requested_model or member.model,
                    role=CouncilRole(member.role),
                    label=member.label,
                )
                for member in row.members
            ],
            mode=row.mode,
            execution_mode=CouncilExecutionMode(row.execution_mode or "council"),
            synthesizer_provider=row.synthesizer_provider,
            synthesizer_model=row.synthesizer_model,
            reviewer_provider=self._orchestration_config(row).get("reviewer_provider"),
            reviewer_model=self._orchestration_config(row).get("reviewer_model"),
            arbiter_provider=self._orchestration_config(row).get("arbiter_provider"),
            arbiter_model=self._orchestration_config(row).get("arbiter_model"),
            delegation_max_calls=int(self._orchestration_config(row).get("delegation_max_calls") or 4),
            delegation_max_depth=1,
            workspace_id=workspace_id,
            correlation_id=None,
            actor_id=row.actor_id,
            member_timeout_seconds=self._member_timeout(row),
        )
        return CouncilRetryPlan(
            request=request,
            reused_member_results=reused,
            failed_member_indexes=failed_indexes,
        )

    def get_retention_policy(
        self,
        workspace_id: str | None,
    ) -> CouncilRetentionPolicy:
        if self._settings_repository is None:
            return CouncilRetentionPolicy(workspace_id=workspace_id)
        row = self._settings_repository.find_one(
            scope="workspace" if workspace_id is not None else "global",
            key=RETENTION_SETTING_KEY,
            workspace_id=workspace_id,
        )
        if row is None or not isinstance(row.value_json, dict):
            return CouncilRetentionPolicy(workspace_id=workspace_id)
        return CouncilRetentionPolicy.model_validate(
            {
                **row.value_json,
                "workspace_id": workspace_id,
            }
        )

    async def update_retention_policy(
        self,
        *,
        workspace_id: str | None,
        update: CouncilRetentionPolicyUpdate,
    ) -> CouncilRetentionPolicy:
        if self._settings_repository is None:
            raise RuntimeError("Council retention settings are unavailable.")
        policy = CouncilRetentionPolicy(
            workspace_id=workspace_id,
            **update.model_dump(),
        )
        self._settings_repository.upsert(
            scope="workspace" if workspace_id is not None else "global",
            key=RETENTION_SETTING_KEY,
            value=update.model_dump(mode="json"),
            workspace_id=workspace_id,
        )
        await self._event_bus.publish(
            Event(
                event_type="council.retention.updated",
                source="council_history",
                workspace_id=workspace_id,
                payload={
                    "retention_days": policy.retention_days,
                    "auto_delete_enabled": policy.auto_delete_enabled,
                    "store_member_answers": policy.store_member_answers,
                    "store_token_usage": policy.store_token_usage,
                },
            )
        )
        return policy

    async def purge_expired(
        self,
        *,
        workspace_id: str | None,
    ) -> CouncilRetentionPurgeResponse:
        policy = self.get_retention_policy(workspace_id)
        deleted_count = await self._purge_expired(
            workspace_id=workspace_id,
            retention_days=policy.retention_days,
        )
        return CouncilRetentionPurgeResponse(
            workspace_id=workspace_id,
            retention_days=policy.retention_days,
            deleted_count=deleted_count,
        )

    async def delete_run(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
    ) -> CouncilRunDeleteResponse | None:
        row = self._repository.get_for_workspace(
            run_id=run_id,
            workspace_id=workspace_id,
        )
        if row is None:
            return None
        self._repository.delete(row)
        await self._event_bus.publish(
            Event(
                event_type="council.history.deleted",
                source="council_history",
                workspace_id=workspace_id,
                payload={"run_id": run_id},
            )
        )
        return CouncilRunDeleteResponse(run_id=run_id, deleted=True)

    async def _publish_saved(self, row: CouncilRunModel) -> None:
        await self._event_bus.publish(
            Event(
                event_type="council.history.saved",
                source="council_history",
                workspace_id=row.workspace_id,
                correlation_id=row.correlation_id,
                payload={
                    "run_id": row.id,
                    "status": row.status,
                    "member_count": row.member_count,
                    "replay_of_run_id": row.replay_of_run_id,
                },
            )
        )

    def _member_rows(
        self,
        run_id: str,
        members: list[CouncilMemberResult],
        *,
        policy: CouncilRetentionPolicy,
    ) -> list[CouncilRunMemberModel]:
        return [
            CouncilRunMemberModel(
                run_id=run_id,
                ordinal=index,
                provider=member.provider,
                model=member.model,
                requested_model=member.requested_model,
                role=member.role.value,
                label=member.label,
                status=member.status,
                fallback_used=member.fallback_used,
                fallback_model=member.fallback_model,
                answer=(
                    member.answer if policy.store_member_answers else ""
                ),
                error_code=member.error_code,
                error_message=member.error_message,
                latency_ms=member.latency_ms,
                input_tokens=(
                    member.usage.input_tokens
                    if policy.store_token_usage
                    else None
                ),
                output_tokens=(
                    member.usage.output_tokens
                    if policy.store_token_usage
                    else None
                ),
                total_tokens=(
                    member.usage.total_tokens
                    if policy.store_token_usage
                    else None
                ),
            )
            for index, member in enumerate(members)
        ]

    async def _purge_expired(
        self,
        *,
        workspace_id: str | None,
        retention_days: int,
    ) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=retention_days
        )
        rows = self._repository.list_expired_for_workspace(
            workspace_id=workspace_id,
            finished_before=cutoff,
        )
        for row in rows:
            self._repository.delete(row)
        if rows:
            await self._event_bus.publish(
                Event(
                    event_type="council.retention.purged",
                    source="council_history",
                    workspace_id=workspace_id,
                    payload={
                        "retention_days": retention_days,
                        "deleted_count": len(rows),
                    },
                )
            )
        return len(rows)

    @staticmethod
    def _summary(row: CouncilRunModel) -> CouncilRunSummary:
        question = " ".join(row.question.split())
        if len(question) > 240:
            question = f"{question[:237].rstrip()}..."
        return CouncilRunSummary(
            run_id=row.id,
            execution_mode=CouncilExecutionMode(row.execution_mode or "council"),
            status=row.status,
            question_preview=question,
            mode=row.mode,
            member_count=row.member_count,
            successful_member_count=row.successful_member_count,
            confidence=row.confidence,
            duration_ms=row.duration_ms,
            replay_of_run_id=row.replay_of_run_id,
            estimated_cost_usd=row.estimated_cost_usd,
            cost_estimate_status=row.cost_estimate_status,
            actual_cost_usd=row.actual_cost_usd,
            actual_cost_status=row.actual_cost_status,
            actual_total_tokens=row.actual_total_tokens,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @classmethod
    def _detail(cls, row: CouncilRunModel) -> CouncilRunHistoryDetail:
        synthesis = None
        if row.synthesis_status is not None:
            synthesis = CouncilSynthesis(
                provider=row.synthesis_provider or "",
                model=row.synthesis_model or "",
                requested_model=row.synthesis_requested_model,
                status=row.synthesis_status,
                fallback_used=row.synthesis_fallback_used,
                fallback_model=row.synthesis_fallback_model,
                final_answer=row.final_answer,
                consensus=list(row.consensus_json or []),
                disagreements=list(row.disagreements_json or []),
                recommendations=list(row.recommendations_json or []),
                confidence=row.confidence,
                usage=CouncilUsage.model_validate(
                    (row.metadata_json or {}).get("synthesis_usage") or {}
                ),
                provider_reported_cost_usd=(
                    (row.metadata_json or {}).get(
                        "synthesis_provider_reported_cost_usd"
                    )
                ),
            )
        return CouncilRunHistoryDetail(
            run_id=row.id,
            execution_mode=CouncilExecutionMode(row.execution_mode or "council"),
            workspace_id=row.workspace_id,
            replay_of_run_id=row.replay_of_run_id,
            estimated_cost_usd=row.estimated_cost_usd,
            cost_estimate_status=row.cost_estimate_status,
            cost_approval_id=row.cost_approval_id,
            cost_reservation_id=row.cost_reservation_id,
            actual_cost_usd=row.actual_cost_usd,
            actual_cost_status=row.actual_cost_status,
            actual_input_tokens=row.actual_input_tokens,
            actual_output_tokens=row.actual_output_tokens,
            actual_total_tokens=row.actual_total_tokens,
            status=row.status,
            question=row.question,
            mode=row.mode,
            members=[cls._member_result(member) for member in row.members],
            synthesis=synthesis,
            member_count=row.member_count,
            successful_member_count=row.successful_member_count,
            correlation_id=row.correlation_id,
            actor_id=row.actor_id,
            error_code=row.error_code,
            error_message=row.error_message or None,
            duration_ms=row.duration_ms,
            started_at=row.started_at,
            finished_at=row.finished_at,
            orchestration=CouncilOrchestrationTrace.model_validate(
                (row.metadata_json or {}).get("orchestration")
                or {"execution_mode": row.execution_mode or "council"}
            ),
        )

    @staticmethod
    def _member_result(row: CouncilRunMemberModel) -> CouncilMemberResult:
        return CouncilMemberResult(
            provider=row.provider,
            model=row.model,
            requested_model=row.requested_model,
            role=CouncilRole(row.role),
            label=row.label,
            status=row.status,
            fallback_used=row.fallback_used,
            fallback_model=row.fallback_model,
            answer=row.answer,
            error_code=row.error_code,
            error_message=row.error_message,
            latency_ms=row.latency_ms,
            usage=CouncilUsage(
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                total_tokens=row.total_tokens,
            ),
        )

    def _settlement(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
        members: list[CouncilMemberResult],
        synthesis: CouncilSynthesis | None,
        auxiliary: list[CouncilAuxiliaryResult] | None = None,
        include_synthesis: bool = True,
    ) -> CouncilCostSettlement:
        entries = [
            self._cost_entry(
                run_id=run_id,
                workspace_id=workspace_id,
                line_key=f"member:{index}",
                kind="member",
                ordinal=index,
                provider=member.provider,
                requested_model=member.requested_model,
                resolved_model=member.model,
                status=member.status,
                usage=member.usage,
                provider_reported_cost_usd=member.provider_reported_cost_usd,
                fallback_used=member.fallback_used,
            )
            for index, member in enumerate(members)
        ]
        for index, item in enumerate(auxiliary or []):
            entries.append(
                self._cost_entry(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    line_key=f"{item.stage}:{index}",
                    kind=item.stage,
                    ordinal=item.ordinal,
                    provider=item.provider,
                    requested_model=item.requested_model,
                    resolved_model=item.model,
                    status=item.status,
                    usage=item.usage,
                    provider_reported_cost_usd=item.provider_reported_cost_usd,
                    fallback_used=bool(item.metadata.get("fallback_used")),
                )
            )
        if synthesis is not None and include_synthesis:
            entries.append(
                self._cost_entry(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    line_key="synthesis",
                    kind="synthesis",
                    ordinal=None,
                    provider=synthesis.provider,
                    requested_model=synthesis.requested_model,
                    resolved_model=synthesis.model,
                    status=synthesis.status,
                    usage=synthesis.usage,
                    provider_reported_cost_usd=synthesis.provider_reported_cost_usd,
                    fallback_used=synthesis.fallback_used,
                )
            )

        costs = [entry.actual_cost_usd for entry in entries]
        known_costs = [value for value in costs if value is not None]
        if entries and len(known_costs) == len(entries):
            status = "known"
        elif known_costs:
            status = "partial"
        else:
            status = "unknown"
        return CouncilCostSettlement(
            entries=entries,
            actual_cost_usd=(round(sum(known_costs), 8) if known_costs else None),
            actual_cost_status=status,
            input_tokens=self._sum_tokens(entries, "input_tokens"),
            output_tokens=self._sum_tokens(entries, "output_tokens"),
            total_tokens=self._sum_tokens(entries, "total_tokens"),
        )

    def _cost_entry(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
        line_key: str,
        kind: str,
        ordinal: int | None,
        provider: str,
        requested_model: str | None,
        resolved_model: str,
        status: str,
        usage: CouncilUsage,
        provider_reported_cost_usd: float | None,
        fallback_used: bool,
    ) -> CouncilCostLedgerModel:
        snapshot: dict[str, Any] = {}
        actual_cost = provider_reported_cost_usd
        source = "provider" if provider_reported_cost_usd is not None else "unknown"

        try:
            model = self._model_repository.find_by_slug(resolved_model)
        except SQLAlchemyError:
            # Some focused unit tests intentionally create only Council tables.
            # In production the catalog exists; without it settlement remains
            # provider-reported/free/unknown instead of breaking history writes.
            model = None
        metadata = dict(model.metadata_json or {}) if model is not None else {}
        billing = str(metadata.get("billing") or ("free" if resolved_model.endswith(":free") else "metered"))
        snapshot["billing"] = billing
        pricing = metadata.get("pricing") if isinstance(metadata.get("pricing"), dict) else metadata
        raw_input = pricing.get("input_per_million_usd")
        raw_output = pricing.get("output_per_million_usd")
        try:
            input_price = float(raw_input)
            output_price = float(raw_output)
        except (TypeError, ValueError):
            input_price = output_price = None
        if input_price is not None and output_price is not None:
            snapshot["input_per_million_usd"] = input_price
            snapshot["output_per_million_usd"] = output_price

        if actual_cost is None and not fallback_used:
            if billing == "free" or resolved_model == "openrouter/free":
                actual_cost = 0.0
                source = "free"
            elif (
                input_price is not None
                and output_price is not None
                and input_price >= 0
                and output_price >= 0
                and usage.input_tokens is not None
                and usage.output_tokens is not None
            ):
                actual_cost = round(
                    usage.input_tokens * input_price / 1_000_000
                    + usage.output_tokens * output_price / 1_000_000,
                    8,
                )
                source = "calculated"

        return CouncilCostLedgerModel(
            run_id=run_id,
            workspace_id=workspace_id,
            line_key=line_key,
            kind=kind,
            ordinal=ordinal,
            provider=provider,
            requested_model=requested_model,
            resolved_model=resolved_model,
            status=status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            provider_reported_cost_usd=provider_reported_cost_usd,
            actual_cost_usd=actual_cost,
            cost_source=source,
            pricing_snapshot_json=snapshot,
        )

    def _settle_reservation(
        self,
        *,
        reservation_id: str | None,
        run_id: str,
        settlement: CouncilCostSettlement,
    ) -> None:
        if not reservation_id:
            return
        row = self._repository.session.get(CouncilCostReservationModel, reservation_id)
        if row is None or row.status != "reserved":
            return
        row.status = "settled"
        row.run_id = run_id
        row.actual_cost_usd = settlement.actual_cost_usd
        row.actual_total_tokens = settlement.total_tokens
        row.settled_at = datetime.now(timezone.utc)
        self._repository.session.flush()

    @staticmethod
    def _sum_tokens(entries: list[CouncilCostLedgerModel], field: str) -> int | None:
        values = [getattr(entry, field) for entry in entries if getattr(entry, field) is not None]
        return sum(values) if values else None


    @staticmethod
    def _orchestration_request_metadata(request: CouncilRunRequest) -> dict[str, Any]:
        return {
            "execution_mode": request.execution_mode.value,
            "reviewer_provider": request.reviewer_provider,
            "reviewer_model": request.reviewer_model,
            "arbiter_provider": request.arbiter_provider,
            "arbiter_model": request.arbiter_model,
            "delegation_max_calls": request.delegation_max_calls,
            "delegation_max_depth": request.delegation_max_depth,
        }

    @staticmethod
    def _orchestration_config(row: CouncilRunModel) -> dict[str, Any]:
        metadata = row.metadata_json or {}
        value = metadata.get("orchestration_request") if isinstance(metadata, dict) else None
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _member_timeout(row: CouncilRunModel) -> int:
        metadata = row.metadata_json or {}
        value = (
            metadata.get("member_timeout_seconds")
            if isinstance(metadata, dict)
            else None
        )
        if isinstance(value, int) and 5 <= value <= 600:
            return value
        return 60
