from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.autonomy.models import (
    MissionCycleModel,
    MissionHypothesisModel,
    MissionStrategyAssignmentModel,
    MissionStrategyEvaluationModel,
    MissionStrategyModel,
    MissionStrategyPolicyModel,
    MissionStrategySelectionModel,
    WorkspaceMissionModel,
)
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.autonomy.strategy_schemas import (
    MissionStrategyAssignmentStatus,
    MissionStrategyAutoSelectRequest,
    MissionStrategyCreate,
    MissionStrategyEvaluateRequest,
    MissionStrategyEvaluationType,
    MissionStrategyFeedbackRequest,
    MissionStrategyPolicyUpsert,
    MissionStrategyRankRequest,
    MissionStrategyRetireRequest,
    MissionStrategySelectRequest,
    MissionStrategySelectionMode,
    MissionStrategyStatus,
    MissionStrategyUpdate,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


ACTIVE_STRATEGY_STATUSES = {
    MissionStrategyStatus.CANDIDATE.value,
    MissionStrategyStatus.SELECTED.value,
}
TERMINAL_ASSIGNMENT_STATUSES = {
    MissionStrategyAssignmentStatus.SUCCEEDED.value,
    MissionStrategyAssignmentStatus.FAILED.value,
    MissionStrategyAssignmentStatus.CANCELLED.value,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


class MissionStrategyNotFound(AutonomousMissionError):
    pass


class MissionStrategyAssignmentNotFound(AutonomousMissionError):
    pass


class MissionStrategyService:
    """Alternative Mission strategies and conservative adaptive selection.

    Automatic selection is disabled by default. A policy must explicitly enable
    it, disable the human-selection requirement and use weighted/adaptive mode.
    The selected strategy is attached to every Mission Cycle and its result is
    fed back into future ranking.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._evaluations = 0
        self._manual_selections = 0
        self._automatic_selections = 0
        self._feedback_updates = 0
        self._cycle_assignments = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = int(
                session.scalar(
                    select(func.count()).select_from(MissionStrategyPolicyModel)
                )
                or 0
            )
            strategies = int(
                session.scalar(select(func.count()).select_from(MissionStrategyModel))
                or 0
            )
            selected = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionStrategyModel)
                    .where(
                        MissionStrategyModel.status
                        == MissionStrategyStatus.SELECTED.value
                    )
                )
                or 0
            )
            assignments = int(
                session.scalar(
                    select(func.count()).select_from(
                        MissionStrategyAssignmentModel
                    )
                )
                or 0
            )
        return {
            "policies": policies,
            "strategies": strategies,
            "selected_strategies": selected,
            "cycle_assignments": assignments,
            "evaluations": self._evaluations,
            "manual_selections": self._manual_selections,
            "automatic_selections": self._automatic_selections,
            "feedback_updates": self._feedback_updates,
            "assignments_created": self._cycle_assignments,
            "default_safety": {
                "selection_mode": "manual",
                "require_human_selection": True,
                "auto_selection_enabled": False,
            },
            "capabilities": [
                "mission_strategy_portfolio",
                "weighted_strategy_scoring",
                "adaptive_strategy_prioritization",
                "safe_automatic_selection",
                "strategy_selection_history",
                "cycle_strategy_assignment",
                "execution_feedback_learning",
                "planner_strategy_context",
            ],
        }

    def get_policy(self, mission_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = session.scalar(
                select(MissionStrategyPolicyModel).where(
                    MissionStrategyPolicyModel.mission_id == mission_id
                )
            )
            return self._policy_to_dict(row, mission)

    async def upsert_policy(
        self,
        mission_id: str,
        request: MissionStrategyPolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            row = session.scalar(
                select(MissionStrategyPolicyModel).where(
                    MissionStrategyPolicyModel.mission_id == mission_id
                )
            )
            if row is None:
                row = MissionStrategyPolicyModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                )
                session.add(row)
            values = request.model_dump()
            for field, value in values.items():
                target = "metadata_json" if field == "metadata" else field
                if field == "selection_mode":
                    value = value.value
                setattr(row, target, value)
            result = self._policy_to_dict(row, mission)

        await self._publish(
            "mission.strategy.policy.updated",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"policy": result},
        )
        return result

    async def create_strategy(
        self,
        mission_id: str,
        request: MissionStrategyCreate,
    ) -> dict[str, Any]:
        requested_selected = request.status == MissionStrategyStatus.SELECTED
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            self._validate_hypothesis(session, mission_id, request.hypothesis_id)
            row = MissionStrategyModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                hypothesis_id=request.hypothesis_id,
                strategy_key=request.strategy_key,
                title=request.title,
                description=request.description,
                strategy_hint=request.strategy_hint,
                status=(
                    MissionStrategyStatus.CANDIDATE.value
                    if requested_selected
                    else request.status.value
                ),
                priority=request.priority,
                expected_value_percent=request.expected_value_percent,
                success_probability_percent=request.success_probability_percent,
                strategic_fit_percent=request.strategic_fit_percent,
                feasibility_percent=request.feasibility_percent,
                evidence_confidence_percent=request.evidence_confidence_percent,
                risk_percent=request.risk_percent,
                cost_percent=request.cost_percent,
                duration_percent=request.duration_percent,
                constraints_json=self._clean_strings(request.constraints),
                tags_json=self._clean_strings(request.tags),
                metadata_json=dict(request.metadata),
            )
            policy = self._policy_row(session, mission)
            calculation = self._calculate_scores(
                row,
                policy,
                total_trials=self._mission_total_trials(session, mission_id),
            )
            row.base_score = calculation["base_score"]
            row.adaptive_score = calculation["adaptive_score"]
            row.last_evaluated_at = utc_now()
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AutonomousMissionError(
                    f"Strategy с ключом {request.strategy_key} уже существует."
                ) from exc
            evaluation = self._add_evaluation(
                session,
                row,
                actor_id="system",
                automatic=True,
                evaluation_type=MissionStrategyEvaluationType.INITIAL.value,
                rationale="Initial strategy score.",
                context={},
                calculation=calculation,
            )
            result = self._strategy_to_dict(row)
            evaluation_result = self._evaluation_to_dict(evaluation)

        self._evaluations += 1
        await self._publish(
            "mission.strategy.created",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"strategy": result, "evaluation": evaluation_result},
        )
        if requested_selected:
            selection = await self.select_strategy(
                result["id"],
                MissionStrategySelectRequest(
                    actor_id="user",
                    rationale="Strategy selected during creation.",
                ),
            )
            return selection["strategy"]
        return result

    def get_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionStrategyModel, strategy_id)
            return None if row is None else self._strategy_to_dict(row)

    def list_strategies(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionStrategyModel).where(
                MissionStrategyModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(MissionStrategyModel.status == status)
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionStrategyModel.adaptive_score.desc(),
                        MissionStrategyModel.base_score.desc(),
                        MissionStrategyModel.priority.desc(),
                        MissionStrategyModel.created_at.asc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._strategy_to_dict(row) for row in rows]

    async def update_strategy(
        self,
        strategy_id: str,
        request: MissionStrategyUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_strategy(session, strategy_id)
            if request.status == MissionStrategyStatus.SELECTED:
                raise MissionStateError(
                    "Используй endpoint select для назначения выбранной Strategy."
                )
            self._validate_hypothesis(
                session,
                row.mission_id,
                request.hypothesis_id,
            )
            updates = request.model_dump(exclude_unset=True)
            score_fields = {
                "expected_value_percent",
                "success_probability_percent",
                "strategic_fit_percent",
                "feasibility_percent",
                "evidence_confidence_percent",
                "risk_percent",
                "cost_percent",
                "duration_percent",
                "priority",
            }
            scores_changed = bool(score_fields.intersection(updates))
            for field, value in updates.items():
                if field == "metadata":
                    target = "metadata_json"
                    value = dict(value or {})
                elif field == "constraints":
                    target = "constraints_json"
                    value = self._clean_strings(value or [])
                elif field == "tags":
                    target = "tags_json"
                    value = self._clean_strings(value or [])
                else:
                    target = field
                    if field == "status" and value is not None:
                        value = value.value
                setattr(row, target, value)
            if row.status in {
                MissionStrategyStatus.RETIRED.value,
                MissionStrategyStatus.REJECTED.value,
            }:
                row.retired_at = row.retired_at or utc_now()
            row.version += 1
            if scores_changed:
                policy = self._policy_row(
                    session, self._require_mission(session, row.mission_id)
                )
                calculation = self._calculate_scores(
                    row,
                    policy,
                    total_trials=self._mission_total_trials(
                        session, row.mission_id
                    ),
                )
                row.base_score = calculation["base_score"]
                row.adaptive_score = calculation["adaptive_score"]
                row.last_evaluated_at = utc_now()
            result = self._strategy_to_dict(row)

        await self._publish(
            "mission.strategy.updated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"strategy": result},
        )
        return result

    async def evaluate_strategy(
        self,
        strategy_id: str,
        request: MissionStrategyEvaluateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_strategy(session, strategy_id)
            for field in (
                "expected_value_percent",
                "success_probability_percent",
                "strategic_fit_percent",
                "feasibility_percent",
                "evidence_confidence_percent",
                "risk_percent",
                "cost_percent",
                "duration_percent",
            ):
                value = getattr(request, field)
                if value is not None:
                    setattr(row, field, value)
            mission = self._require_mission(session, row.mission_id)
            policy = self._policy_row(session, mission)
            calculation = self._calculate_scores(
                row,
                policy,
                total_trials=self._mission_total_trials(session, row.mission_id),
            )
            row.base_score = calculation["base_score"]
            row.adaptive_score = calculation["adaptive_score"]
            row.last_evaluated_at = utc_now()
            row.version += 1
            evaluation = self._add_evaluation(
                session,
                row,
                actor_id=request.actor_id,
                automatic=request.automatic,
                evaluation_type=request.evaluation_type.value,
                rationale=request.rationale,
                context=request.context,
                calculation=calculation,
            )
            strategy = self._strategy_to_dict(row)
            evaluation_result = self._evaluation_to_dict(evaluation)

        self._evaluations += 1
        await self._publish(
            "mission.strategy.evaluated",
            workspace_id=strategy["workspace_id"],
            mission_id=strategy["mission_id"],
            payload={
                "strategy": strategy,
                "evaluation": evaluation_result,
            },
        )
        return {"strategy": strategy, "evaluation": evaluation_result}

    async def rank_portfolio(
        self,
        mission_id: str,
        request: MissionStrategyRankRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission)
            statuses = list(ACTIVE_STRATEGY_STATUSES)
            if request.include_paused:
                statuses.append(MissionStrategyStatus.PAUSED.value)
            rows = list(
                session.scalars(
                    select(MissionStrategyModel).where(
                        MissionStrategyModel.mission_id == mission_id,
                        MissionStrategyModel.status.in_(statuses),
                    )
                ).all()
            )
            total_trials = sum(row.trial_count for row in rows)
            evaluations: list[dict[str, Any]] = []
            for row in rows:
                calculation = self._calculate_scores(
                    row,
                    policy,
                    total_trials=total_trials,
                    adaptive=request.adaptive,
                )
                row.base_score = calculation["base_score"]
                row.adaptive_score = calculation["adaptive_score"]
                row.last_evaluated_at = utc_now()
                if request.persist_evaluations:
                    evaluation = self._add_evaluation(
                        session,
                        row,
                        actor_id=request.actor_id,
                        automatic=True,
                        evaluation_type=(
                            MissionStrategyEvaluationType.ADAPTIVE.value
                            if request.adaptive
                            else MissionStrategyEvaluationType.MANUAL.value
                        ),
                        rationale="Portfolio ranking recalculation.",
                        context=request.context,
                        calculation=calculation,
                    )
                    evaluations.append(self._evaluation_to_dict(evaluation))
            score_field = self._score_field(policy)
            rows.sort(
                key=lambda row: (
                    float(getattr(row, score_field)),
                    row.priority,
                    -int(row.trial_count or 0),
                ),
                reverse=True,
            )
            rows = rows[: int(policy.max_candidates)]
            ranked = []
            for index, row in enumerate(rows, start=1):
                item = self._strategy_to_dict(row)
                item["rank"] = index
                item["selection_score"] = float(getattr(row, score_field))
                ranked.append(item)
            policy_result = self._policy_to_dict(policy, mission)

        if request.persist_evaluations:
            self._evaluations += len(evaluations)
        await self._publish(
            "mission.strategy.portfolio.ranked",
            workspace_id=mission.workspace_id,
            mission_id=mission_id,
            payload={
                "policy": policy_result,
                "ranking": ranked,
                "evaluation_count": len(evaluations),
            },
        )
        return {
            "mission_id": mission_id,
            "policy": policy_result,
            "ranking": ranked,
            "evaluations": evaluations,
        }

    async def select_strategy(
        self,
        strategy_id: str,
        request: MissionStrategySelectRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_strategy(session, strategy_id)
            mission = self._require_mission(session, row.mission_id)
            policy = self._policy_row(session, mission)
            if row.status in {
                MissionStrategyStatus.RETIRED.value,
                MissionStrategyStatus.REJECTED.value,
            }:
                raise MissionStateError(
                    "Retired или rejected Strategy нельзя выбрать."
                )
            score = self._selection_score(row, policy)
            current = session.scalar(
                select(MissionStrategyModel).where(
                    MissionStrategyModel.mission_id == row.mission_id,
                    MissionStrategyModel.status
                    == MissionStrategyStatus.SELECTED.value,
                )
            )
            if current is not None and current.id == row.id:
                return {
                    "strategy": self._strategy_to_dict(row),
                    "selection": None,
                    "changed": False,
                }
            if request.automatic and not request.force:
                self._validate_automatic_selection(
                    session,
                    mission,
                    policy,
                    row,
                    current,
                    score,
                )
            if current is not None:
                current.status = MissionStrategyStatus.CANDIDATE.value
                current.version += 1
            previous_id = None if current is None else current.id
            now = utc_now()
            row.status = MissionStrategyStatus.SELECTED.value
            row.selected_at = now
            row.last_selected_at = now
            row.retired_at = None
            row.version += 1
            selection = MissionStrategySelectionModel(
                workspace_id=row.workspace_id,
                mission_id=row.mission_id,
                strategy_id=row.id,
                previous_strategy_id=previous_id,
                actor_id=request.actor_id,
                automatic=request.automatic,
                selection_mode=policy.selection_mode,
                score_at_selection=score,
                rationale=request.rationale,
                metadata_json={
                    **dict(request.metadata),
                    "mission_cycle_count": mission.cycle_count,
                },
            )
            session.add(selection)
            session.flush()
            result = self._strategy_to_dict(row)
            selection_result = self._selection_to_dict(selection)

        if request.automatic:
            self._automatic_selections += 1
        else:
            self._manual_selections += 1
        await self._publish(
            "mission.strategy.selected",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={
                "strategy": result,
                "selection": selection_result,
                "previous_strategy_id": selection_result["previous_strategy_id"],
            },
        )
        return {
            "strategy": result,
            "selection": selection_result,
            "changed": True,
        }

    async def auto_select(
        self,
        mission_id: str,
        request: MissionStrategyAutoSelectRequest,
    ) -> dict[str, Any]:
        ranking = await self.rank_portfolio(
            mission_id,
            MissionStrategyRankRequest(
                actor_id=request.actor_id,
                adaptive=True,
                persist_evaluations=True,
                context=request.context,
            ),
        )
        candidates = ranking["ranking"]
        if not candidates:
            raise MissionStateError("В Mission нет доступных Strategy-кандидатов.")
        best = candidates[0]
        return await self.select_strategy(
            best["id"],
            MissionStrategySelectRequest(
                actor_id=request.actor_id,
                rationale=request.rationale,
                automatic=True,
                force=request.force,
                metadata={"ranking": candidates[:5], **request.context},
            ),
        )

    async def retire_strategy(
        self,
        strategy_id: str,
        request: MissionStrategyRetireRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_strategy(session, strategy_id)
            row.status = (
                MissionStrategyStatus.REJECTED.value
                if request.rejected
                else MissionStrategyStatus.RETIRED.value
            )
            row.retired_at = utc_now()
            row.version += 1
            metadata = dict(row.metadata_json or {})
            metadata["retirement"] = {
                "actor_id": request.actor_id,
                "rationale": request.rationale,
                "rejected": request.rejected,
                "at": iso(row.retired_at),
            }
            row.metadata_json = metadata
            result = self._strategy_to_dict(row)

        await self._publish(
            "mission.strategy.retired",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"strategy": result, "rationale": request.rationale},
        )
        return result

    def list_evaluations(
        self,
        strategy_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_strategy(session, strategy_id)
            rows = list(
                session.scalars(
                    select(MissionStrategyEvaluationModel)
                    .where(
                        MissionStrategyEvaluationModel.strategy_id == strategy_id
                    )
                    .order_by(MissionStrategyEvaluationModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._evaluation_to_dict(row) for row in rows]

    def list_selections(
        self,
        mission_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            rows = list(
                session.scalars(
                    select(MissionStrategySelectionModel)
                    .where(MissionStrategySelectionModel.mission_id == mission_id)
                    .order_by(MissionStrategySelectionModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._selection_to_dict(row) for row in rows]

    async def assign_cycle(
        self,
        cycle_id: str,
        *,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(MissionStrategyAssignmentModel).where(
                    MissionStrategyAssignmentModel.cycle_id == cycle_id
                )
            )
            if existing is not None:
                return {
                    "assigned": True,
                    "assignment": self._assignment_to_dict(existing),
                    "strategy": self._strategy_to_dict(
                        self._require_strategy(session, existing.strategy_id)
                    ),
                }
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                raise MissionCycleNotFound("Mission Cycle не найден.")
            mission_id = cycle.mission_id
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission)
            selected = session.scalar(
                select(MissionStrategyModel).where(
                    MissionStrategyModel.mission_id == mission_id,
                    MissionStrategyModel.status
                    == MissionStrategyStatus.SELECTED.value,
                )
            )
            should_auto_select = bool(
                policy.enabled
                and policy.auto_selection_enabled
                and not policy.require_human_selection
                and policy.selection_mode
                in {
                    MissionStrategySelectionMode.WEIGHTED.value,
                    MissionStrategySelectionMode.ADAPTIVE.value,
                }
            )

        if should_auto_select:
            try:
                auto_result = await self.auto_select(
                    mission_id,
                    MissionStrategyAutoSelectRequest(
                        actor_id=actor_id,
                        rationale="Automatic selection for Mission Cycle.",
                    ),
                )
                selected_id = auto_result["strategy"]["id"]
            except MissionStateError:
                selected_id = None if selected is None else selected.id
        else:
            selected_id = None if selected is None else selected.id

        if selected_id is None:
            return {
                "assigned": False,
                "assignment": None,
                "strategy": None,
                "reason": "No selected Mission Strategy.",
            }

        with self._session_factory() as session:
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                raise MissionCycleNotFound("Mission Cycle не найден.")
            mission = self._require_mission(session, cycle.mission_id)
            policy = self._policy_row(session, mission)
            strategy = self._require_strategy(session, selected_id)
            assignment = MissionStrategyAssignmentModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                cycle_id=cycle.id,
                strategy_id=strategy.id,
                status=MissionStrategyAssignmentStatus.RUNNING.value,
                selection_mode=policy.selection_mode,
                selected_by=actor_id,
                automatic=should_auto_select,
                score_at_assignment=self._selection_score(strategy, policy),
                selection_reason=(
                    "Adaptive strategy assignment."
                    if should_auto_select
                    else "Current selected strategy assignment."
                ),
                started_at=utc_now(),
                metadata_json={},
            )
            session.add(assignment)
            try:
                session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(MissionStrategyAssignmentModel).where(
                        MissionStrategyAssignmentModel.cycle_id == cycle_id
                    )
                )
                if existing is None:
                    raise
                assignment = existing
            result = self._assignment_to_dict(assignment)
            strategy_result = self._strategy_to_dict(strategy)

        self._cycle_assignments += 1
        await self._publish(
            "mission.strategy.cycle.assigned",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"assignment": result, "strategy": strategy_result},
        )
        return {
            "assigned": True,
            "assignment": result,
            "strategy": strategy_result,
        }

    def build_context(
        self,
        mission_id: str,
        *,
        cycle_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            policy = self._policy_row(session, mission)
            selected = session.scalar(
                select(MissionStrategyModel).where(
                    MissionStrategyModel.mission_id == mission_id,
                    MissionStrategyModel.status
                    == MissionStrategyStatus.SELECTED.value,
                )
            )
            rows = list(
                session.scalars(
                    select(MissionStrategyModel)
                    .where(
                        MissionStrategyModel.mission_id == mission_id,
                        MissionStrategyModel.status.in_(
                            list(ACTIVE_STRATEGY_STATUSES)
                        ),
                    )
                    .order_by(
                        MissionStrategyModel.adaptive_score.desc(),
                        MissionStrategyModel.base_score.desc(),
                    )
                    .limit(max(1, min(100, limit)))
                ).all()
            )
            assignment = None
            if cycle_id is not None:
                assignment_row = session.scalar(
                    select(MissionStrategyAssignmentModel).where(
                        MissionStrategyAssignmentModel.cycle_id == cycle_id
                    )
                )
                if assignment_row is not None:
                    assignment = self._assignment_to_dict(assignment_row)
            return {
                "mission_id": mission_id,
                "workspace_id": mission.workspace_id,
                "policy": self._policy_to_dict(policy, mission),
                "selected_strategy": (
                    None if selected is None else self._strategy_to_dict(selected)
                ),
                "cycle_assignment": assignment,
                "portfolio": [self._strategy_to_dict(row) for row in rows],
            }

    def list_assignments(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionStrategyAssignmentModel).where(
                MissionStrategyAssignmentModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(
                    MissionStrategyAssignmentModel.status == status
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionStrategyAssignmentModel.created_at.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._assignment_to_dict(row) for row in rows]

    async def record_feedback(
        self,
        assignment_id: str,
        request: MissionStrategyFeedbackRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            assignment = self._require_assignment(session, assignment_id)
            status = request.status
            if status is None:
                status = (
                    MissionStrategyAssignmentStatus.SUCCEEDED
                    if request.reward_percent >= 70
                    else MissionStrategyAssignmentStatus.FAILED
                )
            assignment.status = status.value
            assignment.reward_percent = request.reward_percent
            assignment.outcome_summary = request.outcome_summary
            assignment.finished_at = utc_now()
            assignment.metadata_json = {
                **dict(assignment.metadata_json or {}),
                **dict(request.metadata),
                "feedback_actor_id": request.actor_id,
            }
            strategy = self._require_strategy(session, assignment.strategy_id)
            self._recalculate_strategy_performance(session, strategy)
            mission = self._require_mission(session, strategy.mission_id)
            policy = self._policy_row(session, mission)
            calculation = self._calculate_scores(
                strategy,
                policy,
                total_trials=self._mission_total_trials(
                    session, strategy.mission_id
                ),
            )
            strategy.base_score = calculation["base_score"]
            strategy.adaptive_score = calculation["adaptive_score"]
            strategy.last_evaluated_at = utc_now()
            evaluation = self._add_evaluation(
                session,
                strategy,
                actor_id=request.actor_id,
                automatic=request.actor_id == "system",
                evaluation_type=MissionStrategyEvaluationType.FEEDBACK.value,
                rationale=request.outcome_summary or "Cycle outcome feedback.",
                context={"assignment_id": assignment.id, **request.metadata},
                calculation=calculation,
            )
            assignment_result = self._assignment_to_dict(assignment)
            strategy_result = self._strategy_to_dict(strategy)
            evaluation_result = self._evaluation_to_dict(evaluation)

        self._feedback_updates += 1
        self._evaluations += 1
        await self._publish(
            "mission.strategy.feedback.recorded",
            workspace_id=assignment_result["workspace_id"],
            mission_id=assignment_result["mission_id"],
            payload={
                "assignment": assignment_result,
                "strategy": strategy_result,
                "evaluation": evaluation_result,
            },
        )
        return {
            "assignment": assignment_result,
            "strategy": strategy_result,
            "evaluation": evaluation_result,
        }

    async def handle_event(self, event: Event) -> None:
        if event.event_type not in {
            "mission.cycle.plan_created",
            "mission.cycle.completed",
            "mission.cycle.failed",
            "mission.cycle.cancelled",
        }:
            return
        cycle_id = str(event.payload.get("cycle_id") or "")
        if not cycle_id:
            return
        with self._session_factory() as session:
            assignment = session.scalar(
                select(MissionStrategyAssignmentModel).where(
                    MissionStrategyAssignmentModel.cycle_id == cycle_id
                )
            )
            if assignment is None:
                return
            assignment_id = assignment.id
            if (
                event.event_type != "mission.cycle.plan_created"
                and assignment.reward_percent is not None
            ):
                return
            if event.event_type == "mission.cycle.plan_created":
                if assignment.status == MissionStrategyAssignmentStatus.PLANNED.value:
                    assignment.status = MissionStrategyAssignmentStatus.RUNNING.value
                    assignment.started_at = assignment.started_at or utc_now()
                return

        if event.event_type == "mission.cycle.completed":
            reward = 100.0
            status = MissionStrategyAssignmentStatus.SUCCEEDED
            summary = "Mission Cycle completed successfully."
        elif event.event_type == "mission.cycle.cancelled":
            reward = 25.0
            status = MissionStrategyAssignmentStatus.CANCELLED
            summary = str(event.payload.get("error") or "Mission Cycle cancelled.")
        else:
            reward = 0.0
            status = MissionStrategyAssignmentStatus.FAILED
            summary = str(event.payload.get("error") or "Mission Cycle failed.")
        await self.record_feedback(
            assignment_id,
            MissionStrategyFeedbackRequest(
                actor_id="system",
                reward_percent=reward,
                outcome_summary=summary,
                status=status,
                metadata={"event_id": event.id, "event_type": event.event_type},
            ),
        )

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        context = self.build_context(mission_id, limit=20)
        assignments = self.list_assignments(mission_id, limit=20)
        selections = self.list_selections(mission_id, limit=20)
        return {
            **context,
            "recent_assignments": assignments,
            "recent_selections": selections,
            "summary": {
                "strategy_count": len(context["portfolio"]),
                "selected": context["selected_strategy"] is not None,
                "completed_trials": sum(
                    1
                    for item in assignments
                    if item["status"] in TERMINAL_ASSIGNMENT_STATUSES
                ),
            },
        }

    @staticmethod
    def _base_score(row: MissionStrategyModel) -> tuple[float, dict[str, float]]:
        components = {
            "expected_value": row.expected_value_percent * 0.18,
            "success_probability": row.success_probability_percent * 0.18,
            "strategic_fit": row.strategic_fit_percent * 0.16,
            "feasibility": row.feasibility_percent * 0.14,
            "evidence_confidence": row.evidence_confidence_percent * 0.10,
            "risk_inverse": (100.0 - row.risk_percent) * 0.10,
            "cost_inverse": (100.0 - row.cost_percent) * 0.06,
            "duration_inverse": (100.0 - row.duration_percent) * 0.04,
            "priority": float(row.priority) * 0.04,
        }
        return clamp(sum(components.values())), {
            key: round(value, 4) for key, value in components.items()
        }

    def _calculate_scores(
        self,
        row: MissionStrategyModel,
        policy: MissionStrategyPolicyModel,
        *,
        total_trials: int,
        adaptive: bool = True,
    ) -> dict[str, Any]:
        base_score, components = self._base_score(row)
        average_reward = float(
            50.0
            if row.average_reward_percent is None
            else row.average_reward_percent
        )
        trial_count = int(row.trial_count or 0)
        performance_adjustment = (
            (average_reward - 50.0)
            / 50.0
            * float(policy.performance_weight_percent)
        )
        exploration_bonus = 0.0
        if adaptive:
            exploration_bonus = float(policy.exploration_weight_percent) * min(
                1.0,
                math.sqrt(
                    math.log(max(2, total_trials + 2))
                    / max(1, trial_count + 1)
                ),
            )
        adaptive_score = clamp(
            base_score + performance_adjustment + exploration_bonus
        )
        return {
            "base_score": base_score,
            "adaptive_score": adaptive_score,
            "components": components,
            "performance_adjustment": round(performance_adjustment, 4),
            "exploration_bonus": round(exploration_bonus, 4),
            "total_trials": total_trials,
            "strategy_trials": trial_count,
            "average_reward_percent": average_reward,
            "weights": {
                "performance_weight_percent": policy.performance_weight_percent,
                "exploration_weight_percent": policy.exploration_weight_percent,
            },
        }

    def _validate_automatic_selection(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        policy: MissionStrategyPolicyModel,
        candidate: MissionStrategyModel,
        current: MissionStrategyModel | None,
        score: float,
    ) -> None:
        if not policy.enabled:
            raise MissionStateError("Mission Strategy Policy отключена.")
        if not policy.auto_selection_enabled:
            raise MissionStateError("Автоматический выбор Strategy отключён.")
        if policy.require_human_selection:
            raise MissionStateError("Strategy требует ручного подтверждения.")
        if policy.selection_mode == MissionStrategySelectionMode.MANUAL.value:
            raise MissionStateError("Manual policy не разрешает auto-selection.")
        if score < policy.min_selection_score:
            raise MissionStateError(
                f"Strategy score {score} ниже порога {policy.min_selection_score}."
            )
        if current is not None:
            current_score = self._selection_score(current, policy)
            improvement = score - current_score
            if improvement < policy.min_improvement_percent:
                raise MissionStateError(
                    "Улучшение Strategy недостаточно для автоматической замены."
                )
        latest = session.scalar(
            select(MissionStrategySelectionModel)
            .where(MissionStrategySelectionModel.mission_id == mission.id)
            .order_by(MissionStrategySelectionModel.created_at.desc())
            .limit(1)
        )
        if latest is not None and policy.cooldown_cycles > 0:
            selected_cycle_count = int(
                (latest.metadata_json or {}).get("mission_cycle_count", 0)
            )
            if mission.cycle_count - selected_cycle_count < policy.cooldown_cycles:
                raise MissionStateError(
                    "Действует cooldown между автоматическими сменами Strategy."
                )

    @staticmethod
    def _score_field(policy: MissionStrategyPolicyModel) -> str:
        if policy.selection_mode == MissionStrategySelectionMode.ADAPTIVE.value:
            return "adaptive_score"
        return "base_score"

    def _selection_score(
        self,
        row: MissionStrategyModel,
        policy: MissionStrategyPolicyModel,
    ) -> float:
        return float(getattr(row, self._score_field(policy)))

    def _recalculate_strategy_performance(
        self,
        session: Session,
        strategy: MissionStrategyModel,
    ) -> None:
        rows = list(
            session.scalars(
                select(MissionStrategyAssignmentModel).where(
                    MissionStrategyAssignmentModel.strategy_id == strategy.id,
                    MissionStrategyAssignmentModel.reward_percent.is_not(None),
                )
            ).all()
        )
        strategy.trial_count = len(rows)
        strategy.success_count = sum(
            1
            for row in rows
            if row.status == MissionStrategyAssignmentStatus.SUCCEEDED.value
        )
        strategy.failure_count = sum(
            1
            for row in rows
            if row.status == MissionStrategyAssignmentStatus.FAILED.value
        )
        strategy.average_reward_percent = clamp(
            sum(float(row.reward_percent or 0.0) for row in rows) / len(rows)
            if rows
            else 50.0
        )
        strategy.version += 1

    def _mission_total_trials(self, session: Session, mission_id: str) -> int:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(MissionStrategyModel.trial_count), 0))
                .select_from(MissionStrategyModel)
                .where(MissionStrategyModel.mission_id == mission_id)
            )
            or 0
        )

    def _add_evaluation(
        self,
        session: Session,
        row: MissionStrategyModel,
        *,
        actor_id: str,
        automatic: bool,
        evaluation_type: str,
        rationale: str,
        context: dict[str, Any],
        calculation: dict[str, Any],
    ) -> MissionStrategyEvaluationModel:
        evaluation = MissionStrategyEvaluationModel(
            workspace_id=row.workspace_id,
            mission_id=row.mission_id,
            strategy_id=row.id,
            actor_id=actor_id,
            automatic=automatic,
            evaluation_type=evaluation_type,
            expected_value_percent=row.expected_value_percent,
            success_probability_percent=row.success_probability_percent,
            strategic_fit_percent=row.strategic_fit_percent,
            feasibility_percent=row.feasibility_percent,
            evidence_confidence_percent=row.evidence_confidence_percent,
            risk_percent=row.risk_percent,
            cost_percent=row.cost_percent,
            duration_percent=row.duration_percent,
            base_score=calculation["base_score"],
            adaptive_score=calculation["adaptive_score"],
            rationale=rationale,
            calculation_json=dict(calculation),
            context_json=dict(context),
        )
        session.add(evaluation)
        session.flush()
        return evaluation

    def _policy_row(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
    ) -> MissionStrategyPolicyModel:
        row = session.scalar(
            select(MissionStrategyPolicyModel).where(
                MissionStrategyPolicyModel.mission_id == mission.id
            )
        )
        if row is None:
            row = MissionStrategyPolicyModel(
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
            )
            session.add(row)
            session.flush()
        return row

    def _require_mission(
        self,
        session: Session,
        mission_id: str,
    ) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionNotFound("Mission не найдена.")
        return row

    def _require_strategy(
        self,
        session: Session,
        strategy_id: str,
    ) -> MissionStrategyModel:
        row = session.get(MissionStrategyModel, strategy_id)
        if row is None:
            raise MissionStrategyNotFound("Mission Strategy не найдена.")
        return row

    def _require_assignment(
        self,
        session: Session,
        assignment_id: str,
    ) -> MissionStrategyAssignmentModel:
        row = session.get(MissionStrategyAssignmentModel, assignment_id)
        if row is None:
            raise MissionStrategyAssignmentNotFound(
                "Mission Strategy Assignment не найден."
            )
        return row

    @staticmethod
    def _validate_hypothesis(
        session: Session,
        mission_id: str,
        hypothesis_id: str | None,
    ) -> None:
        if hypothesis_id is None:
            return
        row = session.get(MissionHypothesisModel, hypothesis_id)
        if row is None or row.mission_id != mission_id:
            raise AutonomousMissionError(
                "Hypothesis не найдена в указанной Mission."
            )

    @staticmethod
    def _clean_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    def _policy_to_dict(
        self,
        row: MissionStrategyPolicyModel | None,
        mission: WorkspaceMissionModel,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "enabled": True,
                "selection_mode": "manual",
                "require_human_selection": True,
                "auto_selection_enabled": False,
                "min_selection_score": 60.0,
                "min_improvement_percent": 5.0,
                "exploration_weight_percent": 8.0,
                "performance_weight_percent": 20.0,
                "cooldown_cycles": 1,
                "max_candidates": 20,
                "metadata": {},
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "enabled": row.enabled,
            "selection_mode": row.selection_mode,
            "require_human_selection": row.require_human_selection,
            "auto_selection_enabled": row.auto_selection_enabled,
            "min_selection_score": row.min_selection_score,
            "min_improvement_percent": row.min_improvement_percent,
            "exploration_weight_percent": row.exploration_weight_percent,
            "performance_weight_percent": row.performance_weight_percent,
            "cooldown_cycles": row.cooldown_cycles,
            "max_candidates": row.max_candidates,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _strategy_to_dict(row: MissionStrategyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "hypothesis_id": row.hypothesis_id,
            "strategy_key": row.strategy_key,
            "title": row.title,
            "description": row.description,
            "strategy_hint": row.strategy_hint,
            "status": row.status,
            "priority": row.priority,
            "expected_value_percent": row.expected_value_percent,
            "success_probability_percent": row.success_probability_percent,
            "strategic_fit_percent": row.strategic_fit_percent,
            "feasibility_percent": row.feasibility_percent,
            "evidence_confidence_percent": row.evidence_confidence_percent,
            "risk_percent": row.risk_percent,
            "cost_percent": row.cost_percent,
            "duration_percent": row.duration_percent,
            "base_score": row.base_score,
            "adaptive_score": row.adaptive_score,
            "trial_count": row.trial_count,
            "success_count": row.success_count,
            "failure_count": row.failure_count,
            "average_reward_percent": row.average_reward_percent,
            "constraints": list(row.constraints_json or []),
            "tags": list(row.tags_json or []),
            "selected_at": iso(row.selected_at),
            "last_selected_at": iso(row.last_selected_at),
            "last_evaluated_at": iso(row.last_evaluated_at),
            "retired_at": iso(row.retired_at),
            "version": row.version,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _evaluation_to_dict(
        row: MissionStrategyEvaluationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "strategy_id": row.strategy_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "evaluation_type": row.evaluation_type,
            "expected_value_percent": row.expected_value_percent,
            "success_probability_percent": row.success_probability_percent,
            "strategic_fit_percent": row.strategic_fit_percent,
            "feasibility_percent": row.feasibility_percent,
            "evidence_confidence_percent": row.evidence_confidence_percent,
            "risk_percent": row.risk_percent,
            "cost_percent": row.cost_percent,
            "duration_percent": row.duration_percent,
            "base_score": row.base_score,
            "adaptive_score": row.adaptive_score,
            "rationale": row.rationale,
            "calculation": dict(row.calculation_json or {}),
            "context": dict(row.context_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _selection_to_dict(
        row: MissionStrategySelectionModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "strategy_id": row.strategy_id,
            "previous_strategy_id": row.previous_strategy_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "selection_mode": row.selection_mode,
            "score_at_selection": row.score_at_selection,
            "rationale": row.rationale,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _assignment_to_dict(
        row: MissionStrategyAssignmentModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "cycle_id": row.cycle_id,
            "strategy_id": row.strategy_id,
            "status": row.status,
            "selection_mode": row.selection_mode,
            "selected_by": row.selected_by,
            "automatic": row.automatic,
            "score_at_assignment": row.score_at_assignment,
            "selection_reason": row.selection_reason,
            "reward_percent": row.reward_percent,
            "outcome_summary": row.outcome_summary,
            "metadata": dict(row.metadata_json or {}),
            "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str,
        mission_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="mission_strategy_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, **payload},
            )
        )
