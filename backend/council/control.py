from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import secrets
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.council.models import (
    CouncilCostApprovalModel,
    CouncilCostReservationModel,
    CouncilPresetModel,
    CouncilRunModel,
)
from backend.council.schemas import (
    CouncilBudgetPolicy,
    CouncilBudgetPolicyUpdate,
    CouncilCostApproval,
    CouncilCostEstimate,
    CouncilCostLine,
    CouncilCostUsage,
    CouncilMemberRequest,
    CouncilPreset,
    CouncilPresetCreate,
    CouncilPresetDeleteResponse,
    CouncilPresetList,
    CouncilPresetUpdate,
    CouncilRoutingChange,
    CouncilRoutingPlan,
    CouncilRunRequest,
)
from backend.database.models import ModelConfigModel
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository


BUDGET_SETTING_KEY = "council.cost.policy"
APPROVAL_TTL_MINUTES = 15
RESERVATION_TTL_MINUTES = 30


class CouncilCostControlError(RuntimeError):
    def __init__(self, message: str, *, estimate: CouncilCostEstimate) -> None:
        super().__init__(message)
        self.estimate = estimate


class CouncilCostBlockedError(CouncilCostControlError):
    pass


class CouncilCostApprovalRequiredError(CouncilCostControlError):
    pass


class CouncilCostApprovalInvalidError(CouncilCostControlError):
    pass


class CouncilControlService:
    """Council presets, cost gates, reservations, usage accounting and routing."""

    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._settings = SettingsRepository(session)
        self._models = ModelRepository(session)

    # ----------------------------- presets -----------------------------
    def list_presets(self, workspace_id: str | None) -> CouncilPresetList:
        statement = select(CouncilPresetModel).where(
            self._workspace_clause(CouncilPresetModel.workspace_id, workspace_id)
        ).order_by(CouncilPresetModel.name.asc(), CouncilPresetModel.created_at.asc())
        rows = list(self._session.scalars(statement).all())
        return CouncilPresetList(
            workspace_id=workspace_id,
            items=[self._preset(row) for row in rows],
        )

    def get_preset(
        self,
        preset_id: str,
        workspace_id: str | None,
    ) -> CouncilPreset | None:
        row = self._session.scalar(
            select(CouncilPresetModel).where(
                CouncilPresetModel.id == preset_id,
                self._workspace_clause(CouncilPresetModel.workspace_id, workspace_id),
            )
        )
        return None if row is None else self._preset(row)

    async def create_preset(
        self,
        payload: CouncilPresetCreate,
        workspace_id: str | None,
    ) -> CouncilPreset:
        self._ensure_unique_preset_name(payload.name, workspace_id)
        row = CouncilPresetModel(
            workspace_id=workspace_id,
            name=payload.name.strip(),
            description=payload.description.strip(),
            mode=payload.mode,
            execution_mode=payload.execution_mode.value,
            members_json=[member.model_dump(mode="json") for member in payload.members],
            synthesizer_provider=payload.synthesizer_provider,
            synthesizer_model=payload.synthesizer_model,
            reviewer_provider=payload.reviewer_provider,
            reviewer_model=payload.reviewer_model,
            arbiter_provider=payload.arbiter_provider,
            arbiter_model=payload.arbiter_model,
            delegation_max_calls=payload.delegation_max_calls,
            delegation_max_depth=payload.delegation_max_depth,
            member_timeout_seconds=payload.member_timeout_seconds,
        )
        self._session.add(row)
        self._session.flush()
        await self._publish_preset("created", row)
        return self._preset(row)

    async def update_preset(
        self,
        preset_id: str,
        payload: CouncilPresetUpdate,
        workspace_id: str | None,
    ) -> CouncilPreset | None:
        row = self._session.scalar(
            select(CouncilPresetModel).where(
                CouncilPresetModel.id == preset_id,
                self._workspace_clause(CouncilPresetModel.workspace_id, workspace_id),
            )
        )
        if row is None:
            return None
        self._ensure_unique_preset_name(payload.name, workspace_id, exclude_id=preset_id)
        row.name = payload.name.strip()
        row.description = payload.description.strip()
        row.mode = payload.mode
        row.execution_mode = payload.execution_mode.value
        row.members_json = [member.model_dump(mode="json") for member in payload.members]
        row.synthesizer_provider = payload.synthesizer_provider
        row.synthesizer_model = payload.synthesizer_model
        row.reviewer_provider = payload.reviewer_provider
        row.reviewer_model = payload.reviewer_model
        row.arbiter_provider = payload.arbiter_provider
        row.arbiter_model = payload.arbiter_model
        row.delegation_max_calls = payload.delegation_max_calls
        row.delegation_max_depth = payload.delegation_max_depth
        row.member_timeout_seconds = payload.member_timeout_seconds
        row.updated_at = datetime.now(timezone.utc)
        self._session.flush()
        await self._publish_preset("updated", row)
        return self._preset(row)

    async def delete_preset(
        self,
        preset_id: str,
        workspace_id: str | None,
    ) -> CouncilPresetDeleteResponse | None:
        row = self._session.scalar(
            select(CouncilPresetModel).where(
                CouncilPresetModel.id == preset_id,
                self._workspace_clause(CouncilPresetModel.workspace_id, workspace_id),
            )
        )
        if row is None:
            return None
        self._session.delete(row)
        self._session.flush()
        await self._event_bus.publish(
            Event(
                event_type="council.preset.deleted",
                source="council_control",
                workspace_id=workspace_id,
                payload={"preset_id": preset_id},
            )
        )
        return CouncilPresetDeleteResponse(preset_id=preset_id, deleted=True)

    # -------------------------- budget policy --------------------------
    def get_budget_policy(self, workspace_id: str | None) -> CouncilBudgetPolicy:
        row = self._settings.find_one(
            scope="workspace" if workspace_id else "global",
            key=BUDGET_SETTING_KEY,
            workspace_id=workspace_id,
        )
        if row is None or not isinstance(row.value_json, dict):
            return CouncilBudgetPolicy(workspace_id=workspace_id)
        try:
            return CouncilBudgetPolicy(workspace_id=workspace_id, **row.value_json)
        except Exception:
            return CouncilBudgetPolicy(workspace_id=workspace_id)

    async def update_budget_policy(
        self,
        workspace_id: str | None,
        payload: CouncilBudgetPolicyUpdate,
    ) -> CouncilBudgetPolicy:
        policy = CouncilBudgetPolicy(
            workspace_id=workspace_id,
            **payload.model_dump(exclude={"workspace_id"}),
        )
        self._settings.upsert(
            scope="workspace" if workspace_id else "global",
            key=BUDGET_SETTING_KEY,
            workspace_id=workspace_id,
            value=policy.model_dump(exclude={"workspace_id"}, mode="json"),
        )
        await self._event_bus.publish(
            Event(
                event_type="council.cost.policy.updated",
                source="council_control",
                workspace_id=workspace_id,
                payload={
                    "monthly_budget_usd": policy.monthly_budget_usd,
                    "per_run_soft_limit_usd": policy.per_run_soft_limit_usd,
                    "per_run_hard_limit_usd": policy.per_run_hard_limit_usd,
                    "approval_threshold_usd": policy.approval_threshold_usd,
                    "unknown_cost_policy": policy.unknown_cost_policy,
                    "monthly_run_limit": policy.monthly_run_limit,
                    "monthly_token_limit": policy.monthly_token_limit,
                    "per_minute_run_limit": policy.per_minute_run_limit,
                    "routing_mode": policy.routing_mode,
                },
            )
        )
        return policy

    def get_usage(self, workspace_id: str | None) -> CouncilCostUsage:
        """Return current-month actual/committed usage plus active reservations."""
        now = datetime.now(timezone.utc)
        self._expire_reservations(now, workspace_id)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        policy = self.get_budget_policy(workspace_id)

        run_rows = list(
            self._session.execute(
                select(
                    CouncilRunModel.actual_cost_usd,
                    CouncilRunModel.actual_cost_status,
                    CouncilRunModel.estimated_cost_usd,
                    CouncilRunModel.actual_total_tokens,
                ).where(
                    self._workspace_clause(CouncilRunModel.workspace_id, workspace_id),
                    CouncilRunModel.started_at >= month_start,
                )
            ).all()
        )
        actual_spend = round(
            sum(float(row.actual_cost_usd or 0.0) for row in run_rows),
            8,
        )
        committed_spend = 0.0
        actual_tokens = 0
        for row in run_rows:
            if row.actual_cost_status == "known" and row.actual_cost_usd is not None:
                committed_spend += float(row.actual_cost_usd)
            elif row.estimated_cost_usd is not None:
                committed_spend += float(row.estimated_cost_usd)
            elif row.actual_cost_usd is not None:
                committed_spend += float(row.actual_cost_usd)
            actual_tokens += int(row.actual_total_tokens or 0)

        reservations = list(
            self._session.scalars(
                select(CouncilCostReservationModel).where(
                    self._workspace_clause(
                        CouncilCostReservationModel.workspace_id,
                        workspace_id,
                    ),
                    CouncilCostReservationModel.status == "reserved",
                    CouncilCostReservationModel.expires_at > now,
                    CouncilCostReservationModel.created_at >= month_start,
                )
            ).all()
        )
        reserved_spend = round(
            sum(float(row.estimated_cost_usd or 0.0) for row in reservations),
            8,
        )
        reserved_tokens = sum(int(row.estimated_tokens or 0) for row in reservations)
        committed_total = round(committed_spend + reserved_spend, 8)

        return CouncilCostUsage(
            workspace_id=workspace_id,
            month=month_start.strftime("%Y-%m"),
            actual_spend_usd=actual_spend,
            committed_spend_usd=committed_total,
            reserved_spend_usd=reserved_spend,
            completed_runs=len(run_rows),
            reserved_runs=len(reservations),
            actual_total_tokens=actual_tokens,
            reserved_tokens=reserved_tokens,
            monthly_budget_usd=policy.monthly_budget_usd,
            monthly_run_limit=policy.monthly_run_limit,
            monthly_token_limit=policy.monthly_token_limit,
            budget_utilization_percent=self._percent(
                committed_total,
                policy.monthly_budget_usd,
            ),
            run_utilization_percent=self._percent(
                len(run_rows) + len(reservations),
                policy.monthly_run_limit,
            ),
            token_utilization_percent=self._percent(
                actual_tokens + reserved_tokens,
                policy.monthly_token_limit,
            ),
        )

    # ---------------------------- costing ------------------------------
    def estimate(
        self,
        request: CouncilRunRequest,
        workspace_id: str | None,
    ) -> CouncilCostEstimate:
        policy = self.get_budget_policy(workspace_id)
        lines = self._cost_lines_for_request(request, policy)

        known_cost = round(
            sum(line.estimated_cost_usd or 0.0 for line in lines),
            6,
        )
        unknown_models = sorted(
            {line.model for line in lines if not line.pricing_known}
        )
        if not unknown_models:
            status = "known"
            estimated: float | None = known_cost
        elif known_cost > 0:
            status = "partial"
            estimated = None
        else:
            status = "unknown"
            estimated = None

        usage = self.get_usage(workspace_id)
        monthly_spend = usage.committed_spend_usd
        estimated_tokens = sum(
            line.input_tokens_estimate + line.output_tokens_estimate
            for line in lines
        )
        projected = (
            round(monthly_spend + estimated, 6)
            if estimated is not None
            else None
        )
        projected_run_count = usage.completed_runs + usage.reserved_runs + 1
        projected_tokens = (
            usage.actual_total_tokens
            + usage.reserved_tokens
            + estimated_tokens
        )
        reasons: list[str] = []
        decision = "allow"

        if unknown_models:
            if policy.unknown_cost_policy == "block":
                decision = "blocked"
                reasons.append("unknown_metered_cost_blocked")
            elif policy.unknown_cost_policy == "require_approval":
                decision = "approval_required"
                reasons.append("unknown_metered_cost_requires_approval")

        if estimated is not None:
            if (
                policy.per_run_hard_limit_usd > 0
                and estimated > policy.per_run_hard_limit_usd
            ):
                decision = "blocked"
                reasons.append("per_run_hard_limit_exceeded")
            elif (
                policy.per_run_soft_limit_usd > 0
                and estimated > policy.per_run_soft_limit_usd
                and decision != "blocked"
            ):
                decision = "approval_required"
                reasons.append("per_run_soft_limit_exceeded")

            if (
                policy.approval_threshold_usd > 0
                and estimated >= policy.approval_threshold_usd
                and decision != "blocked"
            ):
                decision = "approval_required"
                reasons.append("approval_threshold_reached")

            if (
                policy.monthly_budget_usd > 0
                and projected is not None
                and projected > policy.monthly_budget_usd
            ):
                decision = "blocked"
                reasons.append("workspace_monthly_budget_exceeded")

        if (
            policy.monthly_run_limit > 0
            and projected_run_count > policy.monthly_run_limit
        ):
            decision = "blocked"
            reasons.append("workspace_monthly_run_quota_exceeded")

        if (
            policy.monthly_token_limit > 0
            and projected_tokens > policy.monthly_token_limit
        ):
            decision = "blocked"
            reasons.append("workspace_monthly_token_quota_exceeded")

        if policy.per_minute_run_limit > 0:
            recent = self._recent_reservation_count(workspace_id)
            if recent >= policy.per_minute_run_limit:
                decision = "blocked"
                reasons.append("workspace_run_rate_limit_exceeded")

        return CouncilCostEstimate(
            workspace_id=workspace_id,
            estimate_status=status,
            estimated_cost_usd=estimated,
            known_cost_usd=known_cost,
            unknown_models=unknown_models,
            monthly_estimated_spend_usd=monthly_spend,
            monthly_actual_spend_usd=usage.actual_spend_usd,
            monthly_reserved_spend_usd=usage.reserved_spend_usd,
            monthly_run_count=usage.completed_runs,
            monthly_reserved_run_count=usage.reserved_runs,
            monthly_total_tokens=usage.actual_total_tokens,
            monthly_reserved_tokens=usage.reserved_tokens,
            projected_monthly_spend_usd=projected,
            projected_monthly_run_count=projected_run_count,
            projected_monthly_tokens=projected_tokens,
            decision=decision,
            approval_required=decision == "approval_required",
            reasons=reasons,
            fingerprint=self.fingerprint(request, workspace_id),
            lines=lines,
        )

    async def approve(
        self,
        request: CouncilRunRequest,
        workspace_id: str | None,
    ) -> CouncilCostApproval:
        estimate = self.estimate(request, workspace_id)
        if estimate.decision == "blocked":
            raise CouncilCostBlockedError(
                "Запуск заблокирован политикой стоимости Workspace.",
                estimate=estimate,
            )
        now = datetime.now(timezone.utc)
        row = CouncilCostApprovalModel(
            token=secrets.token_urlsafe(36),
            workspace_id=workspace_id,
            fingerprint=estimate.fingerprint,
            estimated_cost_usd=estimate.estimated_cost_usd,
            estimate_status=estimate.estimate_status,
            reasons_json=list(estimate.reasons),
            actor_id=request.actor_id,
            expires_at=now + timedelta(minutes=APPROVAL_TTL_MINUTES),
        )
        self._session.add(row)
        self._session.flush()
        await self._event_bus.publish(
            Event(
                event_type="council.cost.approved",
                source="council_control",
                workspace_id=workspace_id,
                correlation_id=request.correlation_id,
                payload={
                    "approval_id": row.id,
                    "fingerprint": row.fingerprint,
                    "estimate_status": row.estimate_status,
                    "estimated_cost_usd": row.estimated_cost_usd,
                },
            )
        )
        return CouncilCostApproval(
            approval_id=row.id,
            token=row.token,
            workspace_id=workspace_id,
            fingerprint=row.fingerprint,
            estimated_cost_usd=row.estimated_cost_usd,
            estimate_status=row.estimate_status,
            expires_at=row.expires_at,
        )

    async def authorize(
        self,
        request: CouncilRunRequest,
        workspace_id: str | None,
    ) -> tuple[CouncilRunRequest, CouncilCostEstimate]:
        estimate = self.estimate(request, workspace_id)
        if estimate.decision == "blocked":
            raise CouncilCostBlockedError(
                "Запуск заблокирован политикой стоимости Workspace.",
                estimate=estimate,
            )

        approval_id: str | None = None
        if estimate.approval_required:
            token = (request.cost_approval_token or "").strip()
            if not token:
                raise CouncilCostApprovalRequiredError(
                    "Для запуска требуется подтверждение стоимости.",
                    estimate=estimate,
                )
            row = self._session.scalar(
                select(CouncilCostApprovalModel).where(
                    CouncilCostApprovalModel.token == token,
                    self._workspace_clause(
                        CouncilCostApprovalModel.workspace_id,
                        workspace_id,
                    ),
                )
            )
            now = datetime.now(timezone.utc)
            if (
                row is None
                or row.used_at is not None
                or self._as_utc(row.expires_at) <= now
                or row.fingerprint != estimate.fingerprint
            ):
                raise CouncilCostApprovalInvalidError(
                    "Подтверждение стоимости недействительно, использовано или устарело.",
                    estimate=estimate,
                )
            row.used_at = now
            approval_id = row.id
            self._session.flush()

        estimated_tokens = sum(
            line.input_tokens_estimate + line.output_tokens_estimate
            for line in estimate.lines
        )
        now = datetime.now(timezone.utc)
        reservation = CouncilCostReservationModel(
            workspace_id=workspace_id,
            fingerprint=estimate.fingerprint,
            status="reserved",
            estimate_status=estimate.estimate_status,
            estimated_cost_usd=estimate.estimated_cost_usd,
            estimated_tokens=estimated_tokens,
            actor_id=request.actor_id,
            expires_at=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
        )
        self._session.add(reservation)
        self._session.flush()

        authorized = request.model_copy(
            update={
                "workspace_id": workspace_id,
                "estimated_cost_usd": estimate.estimated_cost_usd,
                "cost_estimate_status": estimate.estimate_status,
                "cost_approval_id": approval_id,
                "cost_reservation_id": reservation.id,
            }
        )
        await self._event_bus.publish(
            Event(
                event_type="council.cost.authorized",
                source="council_control",
                workspace_id=workspace_id,
                correlation_id=request.correlation_id,
                payload={
                    "decision": estimate.decision,
                    "estimate_status": estimate.estimate_status,
                    "estimated_cost_usd": estimate.estimated_cost_usd,
                    "approval_id": approval_id,
                    "reservation_id": reservation.id,
                },
            )
        )
        return authorized, estimate

    def route(
        self,
        request: CouncilRunRequest,
        workspace_id: str | None,
    ) -> CouncilRoutingPlan:
        """Recommend a cheaper compatible composition without silently applying it."""
        original = self.estimate(request, workspace_id)
        policy = self.get_budget_policy(workspace_id)
        chair_provider, chair_model = self._chair(request)
        reviewer_provider = request.reviewer_provider
        reviewer_model = request.reviewer_model
        arbiter_provider = request.arbiter_provider
        arbiter_model = request.arbiter_model

        def plan(*, available: bool, routed=None, reason: str, savings=None):
            return CouncilRoutingPlan(
                workspace_id=workspace_id,
                available=available,
                original_estimate=original,
                routed_estimate=routed,
                recommended_members=recommended,
                synthesizer_provider=chair_provider,
                synthesizer_model=routed_chair_model,
                reviewer_provider=reviewer_provider,
                reviewer_model=routed_reviewer_model,
                arbiter_provider=arbiter_provider,
                arbiter_model=routed_arbiter_model,
                changes=changes,
                estimated_savings_usd=savings,
                reason=reason,
            )

        recommended = list(request.members)
        changes: list[CouncilRoutingChange] = []
        routed_chair_model = chair_model
        routed_reviewer_model = reviewer_model
        routed_arbiter_model = arbiter_model

        if policy.routing_mode == "off":
            return plan(available=False, reason="routing_disabled_by_workspace_policy")

        enabled = self._models.list_enabled()
        used = {(member.provider, member.model) for member in request.members}

        for index, member in enumerate(request.members):
            current_line = original.lines[index]
            candidate = self._cheapest_candidate(
                provider=member.provider,
                input_tokens=current_line.input_tokens_estimate,
                output_tokens=current_line.output_tokens_estimate,
                enabled=enabled,
                excluded=used - {(member.provider, member.model)},
            )
            if candidate is None or candidate.model == member.model:
                continue
            if not self._worth_routing(current_line, candidate, policy.routing_min_savings_percent):
                continue
            used.discard((member.provider, member.model))
            used.add((member.provider, candidate.model))
            recommended[index] = member.model_copy(update={"model": candidate.model})
            changes.append(
                CouncilRoutingChange(
                    kind="member",
                    member_index=index,
                    provider=member.provider,
                    from_model=member.model,
                    to_model=candidate.model,
                    from_estimated_cost_usd=current_line.estimated_cost_usd,
                    to_estimated_cost_usd=candidate.estimated_cost_usd or 0.0,
                    estimated_savings_usd=self._savings(
                        current_line.estimated_cost_usd, candidate.estimated_cost_usd
                    ),
                    reason=(
                        "replace_unknown_price"
                        if current_line.estimated_cost_usd is None
                        else "lower_known_cost"
                    ),
                )
            )

        # Reviewer is optimized independently and must remain distinct from writers/chair.
        if reviewer_model:
            reviewer_line = next((line for line in original.lines if line.kind == "reviewer"), None)
            if reviewer_line is not None:
                excluded = set(used)
                excluded.add((chair_provider, routed_chair_model))
                candidate = self._cheapest_candidate(
                    provider=reviewer_provider or request.members[0].provider,
                    input_tokens=reviewer_line.input_tokens_estimate,
                    output_tokens=reviewer_line.output_tokens_estimate,
                    enabled=enabled,
                    excluded=excluded,
                )
                if (
                    candidate is not None
                    and candidate.model != reviewer_model
                    and self._worth_routing(
                        reviewer_line, candidate, policy.routing_min_savings_percent
                    )
                ):
                    routed_reviewer_model = candidate.model
                    changes.append(
                        CouncilRoutingChange(
                            kind="reviewer",
                            provider=reviewer_provider or request.members[0].provider,
                            from_model=reviewer_model,
                            to_model=candidate.model,
                            from_estimated_cost_usd=reviewer_line.estimated_cost_usd,
                            to_estimated_cost_usd=candidate.estimated_cost_usd or 0.0,
                            estimated_savings_usd=self._savings(
                                reviewer_line.estimated_cost_usd,
                                candidate.estimated_cost_usd,
                            ),
                            reason="lower_known_cost",
                        )
                    )

        # Standard modes use the chair as finalizer; arbitration uses an independent arbiter.
        if request.execution_mode.value != "solo":
            final_line = original.lines[-1]
            if request.execution_mode.value == "arbitration" and arbiter_model:
                final_provider = arbiter_provider or request.members[0].provider
                final_model = arbiter_model
                excluded = set(used)
                if routed_reviewer_model:
                    excluded.add((reviewer_provider or request.members[0].provider, routed_reviewer_model))
                change_kind = "synthesis"
            else:
                final_provider = chair_provider
                final_model = chair_model
                excluded = set()
                if request.execution_mode.value == "review" and routed_reviewer_model:
                    excluded.add(
                        (
                            reviewer_provider or request.members[0].provider,
                            routed_reviewer_model,
                        )
                    )
                change_kind = "synthesis"

            candidate = self._cheapest_candidate(
                provider=final_provider,
                input_tokens=final_line.input_tokens_estimate,
                output_tokens=final_line.output_tokens_estimate,
                enabled=enabled,
                excluded=excluded,
            )
            if (
                candidate is not None
                and candidate.model != final_model
                and self._worth_routing(
                    final_line, candidate, policy.routing_min_savings_percent
                )
            ):
                if request.execution_mode.value == "arbitration" and arbiter_model:
                    routed_arbiter_model = candidate.model
                else:
                    routed_chair_model = candidate.model
                changes.append(
                    CouncilRoutingChange(
                        kind=change_kind,
                        provider=final_provider,
                        from_model=final_model,
                        to_model=candidate.model,
                        from_estimated_cost_usd=final_line.estimated_cost_usd,
                        to_estimated_cost_usd=candidate.estimated_cost_usd or 0.0,
                        estimated_savings_usd=self._savings(
                            final_line.estimated_cost_usd,
                            candidate.estimated_cost_usd,
                        ),
                        reason=(
                            "replace_unknown_price"
                            if final_line.estimated_cost_usd is None
                            else "lower_known_cost"
                        ),
                    )
                )

        if not changes:
            return plan(available=False, reason="no_cheaper_compatible_models")

        routed_request = request.model_copy(
            update={
                "members": recommended,
                "synthesizer_provider": chair_provider,
                "synthesizer_model": routed_chair_model,
                "reviewer_provider": reviewer_provider,
                "reviewer_model": routed_reviewer_model,
                "arbiter_provider": arbiter_provider,
                "arbiter_model": routed_arbiter_model,
                "cost_approval_token": None,
            }
        )
        routed = self.estimate(routed_request, workspace_id)
        savings = self._savings(original.estimated_cost_usd, routed.estimated_cost_usd)
        return plan(
            available=True,
            routed=routed,
            savings=savings,
            reason="budget_aware_recommendation",
        )

    def fingerprint(self, request: CouncilRunRequest, workspace_id: str | None) -> str:
        payload = request.model_dump(
            mode="json",
            exclude={
                "cost_approval_token",
                "estimated_cost_usd",
                "cost_estimate_status",
                "cost_approval_id",
                "cost_reservation_id",
            },
        )
        payload["workspace_id"] = workspace_id
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _cost_lines_for_request(
        self, request: CouncilRunRequest, policy: CouncilBudgetPolicy
    ) -> list[CouncilCostLine]:
        question_tokens = self._estimate_tokens(request.question)
        lines: list[CouncilCostLine] = []
        member_output = policy.expected_member_output_tokens
        synthesis_output = policy.expected_synthesis_output_tokens

        for member in request.members:
            lines.append(
                self._cost_line(
                    kind="member",
                    provider=member.provider,
                    model_slug=member.model,
                    label=member.label or f"{member.role.value} · {member.model}",
                    model=self._models.find_by_slug(member.model),
                    input_tokens=question_tokens + 600,
                    output_tokens=member_output,
                )
            )

        if request.execution_mode.value == "solo":
            return lines

        chair_provider, chair_model = self._chair(request)
        chair_row = self._models.find_by_slug(chair_model)
        base_synthesis_input = question_tokens + 900 + len(request.members) * member_output

        if request.execution_mode.value == "review":
            lines.append(
                self._cost_line(
                    kind="draft",
                    provider=chair_provider,
                    model_slug=chair_model,
                    label="Председатель / черновик",
                    model=chair_row,
                    input_tokens=base_synthesis_input,
                    output_tokens=synthesis_output,
                )
            )
            reviewer_provider = request.reviewer_provider or request.members[0].provider
            reviewer_model = request.reviewer_model or request.members[0].model
            lines.append(
                self._cost_line(
                    kind="reviewer",
                    provider=reviewer_provider,
                    model_slug=reviewer_model,
                    label="Independent Reviewer",
                    model=self._models.find_by_slug(reviewer_model),
                    input_tokens=base_synthesis_input + synthesis_output,
                    output_tokens=max(500, member_output // 2),
                )
            )
            lines.append(
                self._cost_line(
                    kind="synthesis",
                    provider=chair_provider,
                    model_slug=chair_model,
                    label="Председатель / финальная редакция",
                    model=chair_row,
                    input_tokens=base_synthesis_input + synthesis_output + max(500, member_output // 2),
                    output_tokens=synthesis_output,
                )
            )
            return lines

        if request.execution_mode.value == "arbitration":
            reviewer_provider = request.reviewer_provider or request.members[0].provider
            reviewer_model = request.reviewer_model or request.members[0].model
            lines.append(
                self._cost_line(
                    kind="reviewer",
                    provider=reviewer_provider,
                    model_slug=reviewer_model,
                    label="Independent Reviewer",
                    model=self._models.find_by_slug(reviewer_model),
                    input_tokens=base_synthesis_input,
                    output_tokens=max(500, member_output // 2),
                )
            )
            arbiter_provider = request.arbiter_provider or request.members[0].provider
            arbiter_model = request.arbiter_model or request.members[0].model
            lines.append(
                self._cost_line(
                    kind="synthesis",
                    provider=arbiter_provider,
                    model_slug=arbiter_model,
                    label="Independent Arbiter",
                    model=self._models.find_by_slug(arbiter_model),
                    input_tokens=base_synthesis_input + max(500, member_output // 2),
                    output_tokens=synthesis_output,
                )
            )
            return lines

        if request.execution_mode.value == "delegate":
            lines.append(
                self._cost_line(
                    kind="planner",
                    provider=chair_provider,
                    model_slug=chair_model,
                    label="PRIMARY / delegation planner",
                    model=chair_row,
                    input_tokens=base_synthesis_input,
                    output_tokens=max(400, member_output // 2),
                )
            )
            for index in range(request.delegation_max_calls):
                member = request.members[index % len(request.members)]
                lines.append(
                    self._cost_line(
                        kind="delegate",
                        provider=member.provider,
                        model_slug=member.model,
                        label=f"Delegate #{index + 1}",
                        model=self._models.find_by_slug(member.model),
                        input_tokens=question_tokens + 500,
                        output_tokens=member_output,
                    )
                )
            base_synthesis_input += request.delegation_max_calls * member_output

        label = (
            "Best-of-N / выбор и синтез"
            if request.execution_mode.value == "best_of_n"
            else "Председатель / синтез"
        )
        lines.append(
            self._cost_line(
                kind="synthesis",
                provider=chair_provider,
                model_slug=chair_model,
                label=label,
                model=chair_row,
                input_tokens=base_synthesis_input,
                output_tokens=synthesis_output,
            )
        )
        return lines

    # ---------------------------- helpers ------------------------------
    def _expire_reservations(
        self,
        now: datetime,
        workspace_id: str | None,
    ) -> None:
        rows = list(
            self._session.scalars(
                select(CouncilCostReservationModel).where(
                    self._workspace_clause(
                        CouncilCostReservationModel.workspace_id,
                        workspace_id,
                    ),
                    CouncilCostReservationModel.status == "reserved",
                    CouncilCostReservationModel.expires_at <= now,
                )
            ).all()
        )
        for row in rows:
            row.status = "expired"
        if rows:
            self._session.flush()

    def _recent_reservation_count(self, workspace_id: str | None) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=1)
        value = self._session.scalar(
            select(func.count(CouncilCostReservationModel.id)).where(
                self._workspace_clause(
                    CouncilCostReservationModel.workspace_id,
                    workspace_id,
                ),
                CouncilCostReservationModel.created_at >= cutoff,
                CouncilCostReservationModel.status.in_(("reserved", "settled")),
            )
        )
        return int(value or 0)

    def _cheapest_candidate(
        self,
        *,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        enabled: list[ModelConfigModel],
        excluded: set[tuple[str, str]],
    ) -> CouncilCostLine | None:
        candidates: list[tuple[float, int, CouncilCostLine]] = []
        for model in enabled:
            if model.provider.strip().lower() != provider.strip().lower():
                continue
            if (provider, model.slug) in excluded:
                continue
            metadata = dict(model.metadata_json or {})
            if metadata.get("routing_disabled") is True:
                continue
            line = self._cost_line(
                kind="member",
                provider=provider,
                model_slug=model.slug,
                label=model.display_name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            if not line.pricing_known or line.estimated_cost_usd is None:
                continue
            candidates.append(
                (line.estimated_cost_usd, int(model.priority or 100), line)
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2].model))
        return candidates[0][2]

    @staticmethod
    def _worth_routing(
        current: CouncilCostLine,
        candidate: CouncilCostLine,
        minimum_percent: float,
    ) -> bool:
        if candidate.estimated_cost_usd is None:
            return False
        if current.estimated_cost_usd is None:
            return True
        if candidate.estimated_cost_usd >= current.estimated_cost_usd:
            return False
        if current.estimated_cost_usd <= 0:
            return False
        savings_percent = (
            (current.estimated_cost_usd - candidate.estimated_cost_usd)
            / current.estimated_cost_usd
            * 100
        )
        return savings_percent >= minimum_percent

    @staticmethod
    def _savings(before: float | None, after: float | None) -> float | None:
        if before is None or after is None:
            return None
        return round(max(0.0, before - after), 8)

    @staticmethod
    def _percent(value: float | int, limit: float | int) -> float | None:
        if not limit:
            return None
        return round(float(value) / float(limit) * 100, 2)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Deliberately conservative language-agnostic approximation for preflight.
        return max(1, math.ceil(len(text.encode("utf-8")) / 3.2))

    def _cost_line(
        self,
        *,
        kind: str,
        provider: str,
        model_slug: str,
        label: str,
        model: ModelConfigModel | None,
        input_tokens: int,
        output_tokens: int,
    ) -> CouncilCostLine:
        billing, input_price, output_price, known = self._pricing(model, model_slug)
        cost = None
        if known:
            cost = round(
                input_tokens * input_price / 1_000_000
                + output_tokens * output_price / 1_000_000,
                6,
            )
        return CouncilCostLine(
            kind=kind,
            provider=provider,
            model=model_slug,
            label=label,
            billing=billing,
            input_tokens_estimate=input_tokens,
            output_tokens_estimate=output_tokens,
            estimated_cost_usd=cost,
            pricing_known=known,
        )

    @staticmethod
    def _pricing(
        model: ModelConfigModel | None,
        model_slug: str,
    ) -> tuple[str, float, float, bool]:
        metadata: dict[str, Any] = dict(model.metadata_json or {}) if model is not None else {}
        billing = str(metadata.get("billing") or ("free" if model_slug.endswith(":free") else "metered"))
        if billing == "free" or model_slug == "openrouter/free":
            return billing, 0.0, 0.0, True
        pricing = metadata.get("pricing") if isinstance(metadata.get("pricing"), dict) else metadata
        valid_until = pricing.get("valid_until")
        if valid_until:
            try:
                expires = datetime.fromisoformat(str(valid_until)).date()
            except ValueError:
                return billing, 0.0, 0.0, False
            if datetime.now(timezone.utc).date() > expires:
                return billing, 0.0, 0.0, False
        raw_input = pricing.get("input_per_million_usd")
        raw_output = pricing.get("output_per_million_usd")
        try:
            input_price = float(raw_input)
            output_price = float(raw_output)
        except (TypeError, ValueError):
            return billing, 0.0, 0.0, False
        if input_price < 0 or output_price < 0:
            return billing, 0.0, 0.0, False
        return billing, input_price, output_price, True

    @staticmethod
    def _chair(request: CouncilRunRequest) -> tuple[str, str]:
        first = request.members[0]
        return (
            request.synthesizer_provider or first.provider,
            request.synthesizer_model or first.model,
        )

    def _ensure_unique_preset_name(
        self,
        name: str,
        workspace_id: str | None,
        *,
        exclude_id: str | None = None,
    ) -> None:
        statement = select(CouncilPresetModel).where(
            func.lower(CouncilPresetModel.name) == name.strip().lower(),
            self._workspace_clause(CouncilPresetModel.workspace_id, workspace_id),
        )
        if exclude_id is not None:
            statement = statement.where(CouncilPresetModel.id != exclude_id)
        if self._session.scalar(statement) is not None:
            raise ValueError("Preset with this name already exists in the Workspace.")

    @staticmethod
    def _workspace_clause(column, workspace_id: str | None):
        return column.is_(None) if workspace_id is None else column == workspace_id

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _preset(row: CouncilPresetModel) -> CouncilPreset:
        return CouncilPreset(
            id=row.id,
            workspace_id=row.workspace_id,
            name=row.name,
            description=row.description,
            mode=row.mode,
            execution_mode=row.execution_mode,
            members=[CouncilMemberRequest.model_validate(item) for item in row.members_json],
            synthesizer_provider=row.synthesizer_provider,
            synthesizer_model=row.synthesizer_model,
            reviewer_provider=row.reviewer_provider,
            reviewer_model=row.reviewer_model,
            arbiter_provider=row.arbiter_provider,
            arbiter_model=row.arbiter_model,
            delegation_max_calls=row.delegation_max_calls,
            delegation_max_depth=row.delegation_max_depth,
            member_timeout_seconds=row.member_timeout_seconds,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _publish_preset(self, action: str, row: CouncilPresetModel) -> None:
        await self._event_bus.publish(
            Event(
                event_type=f"council.preset.{action}",
                source="council_control",
                workspace_id=row.workspace_id,
                payload={"preset_id": row.id, "name": row.name},
            )
        )
