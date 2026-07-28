from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.autonomy.governance_schemas import (
    MissionCheckpointCancelRequest,
    MissionCheckpointCreate,
    MissionCheckpointDecision,
    MissionCheckpointDecisionRequest,
    MissionCheckpointEvaluateRequest,
    MissionCheckpointStatus,
    MissionCheckpointType,
    MissionHypothesisCreate,
    MissionHypothesisDecision,
    MissionHypothesisEvaluateRequest,
    MissionHypothesisReopenRequest,
    MissionHypothesisStatus,
    MissionHypothesisUpdate,
    MissionRiskAssessRequest,
    MissionRiskCategory,
    MissionRiskCloseRequest,
    MissionRiskCreate,
    MissionRiskDecision,
    MissionRiskMaterializeRequest,
    MissionRiskSeverity,
    MissionRiskStatus,
    MissionRiskUpdate,
)
from backend.autonomy.models import (
    MissionCheckpointDecisionModel,
    MissionCycleModel,
    MissionDecisionCheckpointModel,
    MissionEvidenceModel,
    MissionGoalModel,
    MissionHypothesisEvaluationModel,
    MissionHypothesisModel,
    MissionRiskAssessmentModel,
    MissionRiskModel,
    WorkspaceMissionModel,
)
from backend.autonomy.schemas import MissionStatus
from backend.autonomy.service import (
    AutonomousMissionError,
    MissionCycleNotFound,
    MissionGoalNotFound,
    MissionNotFound,
    MissionStateError,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


OPEN_RISK_STATUSES = {
    MissionRiskStatus.IDENTIFIED.value,
    MissionRiskStatus.MONITORING.value,
    MissionRiskStatus.MATERIALIZED.value,
}
OPEN_CHECKPOINT_STATUSES = {
    MissionCheckpointStatus.PENDING.value,
    MissionCheckpointStatus.READY.value,
    MissionCheckpointStatus.DEFERRED.value,
}
TERMINAL_HYPOTHESIS_STATUSES = {
    MissionHypothesisStatus.SUPPORTED.value,
    MissionHypothesisStatus.REJECTED.value,
    MissionHypothesisStatus.INVALIDATED.value,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    normalized = ensure_utc(value)
    return None if normalized is None else normalized.isoformat()


def calculate_exposure(
    probability_percent: float,
    impact_percent: float,
) -> tuple[float, str]:
    exposure = round(
        max(0.0, min(100.0, probability_percent))
        * max(0.0, min(100.0, impact_percent))
        / 100.0,
        2,
    )
    if exposure >= 75:
        severity = MissionRiskSeverity.CRITICAL.value
    elif exposure >= 50:
        severity = MissionRiskSeverity.HIGH.value
    elif exposure >= 25:
        severity = MissionRiskSeverity.MEDIUM.value
    else:
        severity = MissionRiskSeverity.LOW.value
    return exposure, severity


class MissionGovernanceService:
    """Mission risk, hypothesis and decision-checkpoint control.

    All autonomous decisions are conservative. Checkpoints require a human by
    default, and automatic decisions are limited to ``continue`` and
    ``accept_risk`` after an explicit opt-in and a confidence threshold.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._risk_assessments = 0
        self._hypothesis_evaluations = 0
        self._automatic_decisions = 0
        self._blocked_cycles = 0
        self._captured_failures = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            risks = int(
                session.scalar(select(func.count()).select_from(MissionRiskModel))
                or 0
            )
            open_risks = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionRiskModel)
                    .where(MissionRiskModel.status.in_(OPEN_RISK_STATUSES))
                )
                or 0
            )
            hypotheses = int(
                session.scalar(
                    select(func.count()).select_from(MissionHypothesisModel)
                )
                or 0
            )
            checkpoints = int(
                session.scalar(
                    select(func.count()).select_from(
                        MissionDecisionCheckpointModel
                    )
                )
                or 0
            )
            blocking = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionDecisionCheckpointModel)
                    .where(
                        MissionDecisionCheckpointModel.blocking.is_(True),
                        MissionDecisionCheckpointModel.status
                        == MissionCheckpointStatus.READY.value,
                    )
                )
                or 0
            )
        return {
            "risks": risks,
            "open_risks": open_risks,
            "hypotheses": hypotheses,
            "checkpoints": checkpoints,
            "blocking_checkpoints": blocking,
            "risk_assessments": self._risk_assessments,
            "hypothesis_evaluations": self._hypothesis_evaluations,
            "automatic_decisions": self._automatic_decisions,
            "blocked_cycles": self._blocked_cycles,
            "captured_failures": self._captured_failures,
            "default_safety": {
                "checkpoint_requires_human": True,
                "automatic_decision_enabled": False,
                "automatic_decisions": ["continue", "accept_risk"],
            },
            "capabilities": [
                "mission_risk_register",
                "risk_exposure_scoring",
                "immutable_risk_assessment_history",
                "mission_hypothesis_testing",
                "evidence_weighted_hypothesis_evaluation",
                "decision_checkpoints",
                "human_decision_gates",
                "safe_automatic_checkpoint_decisions",
                "mission_cycle_admission_guard",
                "execution_failure_risk_capture",
            ],
        }

    async def create_risk(
        self,
        mission_id: str,
        request: MissionRiskCreate,
    ) -> dict[str, Any]:
        checkpoint: dict[str, Any] | None = None
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if request.goal_id is not None:
                self._require_goal(session, mission_id, request.goal_id)
            exposure, severity = calculate_exposure(
                request.probability_percent,
                request.impact_percent,
            )
            row = MissionRiskModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=request.goal_id,
                risk_key=request.risk_key,
                title=request.title,
                description=request.description,
                category=request.category.value,
                status=MissionRiskStatus.IDENTIFIED.value,
                probability_percent=request.probability_percent,
                impact_percent=request.impact_percent,
                exposure_score=exposure,
                severity=severity,
                owner_id=request.owner_id,
                mitigation_plan=request.mitigation_plan,
                contingency_plan=request.contingency_plan,
                trigger_indicators_json=self._clean_strings(
                    request.trigger_indicators
                ),
                checkpoint_required=request.checkpoint_required,
                requires_human_decision=request.requires_human_decision,
                due_at=ensure_utc(request.due_at),
                next_review_at=ensure_utc(request.next_review_at),
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AutonomousMissionError(
                    f"Risk с ключом {request.risk_key} уже существует."
                ) from exc
            if row.checkpoint_required and severity in {
                MissionRiskSeverity.HIGH.value,
                MissionRiskSeverity.CRITICAL.value,
            }:
                checkpoint_row = self._ensure_risk_checkpoint(session, row)
                checkpoint = self._checkpoint_to_dict(checkpoint_row)
            result = self._risk_to_dict(row)

        await self._publish(
            "mission.risk.created",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"risk": result, "checkpoint": checkpoint},
        )
        return {"risk": result, "checkpoint": checkpoint}

    def get_risk(self, risk_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionRiskModel, risk_id)
            return None if row is None else self._risk_to_dict(row)

    def list_risks(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        severity: str | None = None,
        goal_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionRiskModel).where(
                MissionRiskModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(MissionRiskModel.status == status)
            if severity is not None:
                statement = statement.where(MissionRiskModel.severity == severity)
            if goal_id is not None:
                statement = statement.where(MissionRiskModel.goal_id == goal_id)
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionRiskModel.exposure_score.desc(),
                        MissionRiskModel.updated_at.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._risk_to_dict(row) for row in rows]

    async def update_risk(
        self,
        risk_id: str,
        request: MissionRiskUpdate,
    ) -> dict[str, Any]:
        checkpoint: dict[str, Any] | None = None
        with self._session_factory() as session:
            row = self._require_risk(session, risk_id)
            if request.goal_id is not None:
                self._require_goal(session, row.mission_id, request.goal_id)
            updates = request.model_dump(exclude_unset=True)
            enum_fields = {"category"}
            json_fields = {
                "trigger_indicators": "trigger_indicators_json",
                "metadata": "metadata_json",
            }
            for field, value in updates.items():
                if field in json_fields:
                    target = json_fields[field]
                    if field == "trigger_indicators":
                        value = self._clean_strings(value or [])
                    else:
                        value = dict(value or {})
                else:
                    target = field
                    if field in enum_fields and value is not None:
                        value = value.value
                    if field.endswith("_at"):
                        value = ensure_utc(value)
                setattr(row, target, value)
            row.version += 1
            if row.checkpoint_required and row.severity in {
                MissionRiskSeverity.HIGH.value,
                MissionRiskSeverity.CRITICAL.value,
            } and row.status in OPEN_RISK_STATUSES:
                cp = self._ensure_risk_checkpoint(session, row)
                checkpoint = self._checkpoint_to_dict(cp)
            result = self._risk_to_dict(row)

        await self._publish(
            "mission.risk.updated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"risk": result, "checkpoint": checkpoint},
        )
        return {"risk": result, "checkpoint": checkpoint}

    async def assess_risk(
        self,
        risk_id: str,
        request: MissionRiskAssessRequest,
    ) -> dict[str, Any]:
        checkpoint: dict[str, Any] | None = None
        with self._session_factory() as session:
            row = self._require_risk(session, risk_id)
            previous_status = row.status
            probability = (
                row.probability_percent
                if request.probability_percent is None
                else request.probability_percent
            )
            impact = (
                row.impact_percent
                if request.impact_percent is None
                else request.impact_percent
            )
            exposure, severity = calculate_exposure(probability, impact)
            row.probability_percent = probability
            row.impact_percent = impact
            row.exposure_score = exposure
            row.severity = severity
            row.last_assessed_at = utc_now()
            row.version += 1
            row.status = self._risk_status_for_decision(
                request.decision,
                current=row.status,
            )
            if request.decision == MissionRiskDecision.MATERIALIZE:
                row.materialized_at = row.materialized_at or utc_now()
            if row.status in {
                MissionRiskStatus.CLOSED.value,
                MissionRiskStatus.MITIGATED.value,
            }:
                row.closed_at = utc_now()
            assessment = MissionRiskAssessmentModel(
                workspace_id=row.workspace_id,
                mission_id=row.mission_id,
                risk_id=row.id,
                actor_id=request.actor_id,
                automatic=request.automatic,
                decision=request.decision.value,
                probability_percent=probability,
                impact_percent=impact,
                exposure_score=exposure,
                severity=severity,
                rationale=request.rationale,
                indicators_json=dict(request.indicators),
                previous_status=previous_status,
                new_status=row.status,
            )
            session.add(assessment)
            session.flush()
            if (
                request.create_checkpoint
                and row.checkpoint_required
                and row.status in OPEN_RISK_STATUSES
                and severity in {
                    MissionRiskSeverity.HIGH.value,
                    MissionRiskSeverity.CRITICAL.value,
                }
            ):
                cp = self._ensure_risk_checkpoint(session, row)
                checkpoint = self._checkpoint_to_dict(cp)
            elif row.status in {
                MissionRiskStatus.ACCEPTED.value,
                MissionRiskStatus.MITIGATED.value,
                MissionRiskStatus.CLOSED.value,
            }:
                self._resolve_linked_checkpoint(
                    session,
                    risk_id=row.id,
                    actor_id=request.actor_id,
                    rationale=request.rationale,
                    decision=(
                        MissionCheckpointDecision.ACCEPT_RISK.value
                        if row.status == MissionRiskStatus.ACCEPTED.value
                        else MissionCheckpointDecision.CONTINUE.value
                    ),
                    automatic=request.automatic,
                )
            result = self._risk_to_dict(row)
            assessment_result = self._risk_assessment_to_dict(assessment)

        self._risk_assessments += 1
        await self._publish(
            "mission.risk.assessed",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={
                "risk": result,
                "assessment": assessment_result,
                "checkpoint": checkpoint,
            },
        )
        return {
            "risk": result,
            "assessment": assessment_result,
            "checkpoint": checkpoint,
        }

    async def materialize_risk(
        self,
        risk_id: str,
        request: MissionRiskMaterializeRequest,
    ) -> dict[str, Any]:
        return await self.assess_risk(
            risk_id,
            MissionRiskAssessRequest(
                probability_percent=100,
                impact_percent=request.impact_percent,
                decision=MissionRiskDecision.MATERIALIZE,
                rationale=request.rationale,
                indicators=request.indicators,
                actor_id=request.actor_id,
                create_checkpoint=request.create_checkpoint,
            ),
        )

    async def close_risk(
        self,
        risk_id: str,
        request: MissionRiskCloseRequest,
    ) -> dict[str, Any]:
        decision = {
            MissionRiskStatus.ACCEPTED: MissionRiskDecision.ACCEPT,
            MissionRiskStatus.MITIGATED: MissionRiskDecision.MITIGATE,
            MissionRiskStatus.CLOSED: MissionRiskDecision.CLOSE,
        }[request.status]
        result = await self.assess_risk(
            risk_id,
            MissionRiskAssessRequest(
                decision=decision,
                rationale=request.rationale,
                actor_id=request.actor_id,
                create_checkpoint=False,
            ),
        )
        if request.status == MissionRiskStatus.MITIGATED:
            with self._session_factory() as session:
                row = self._require_risk(session, risk_id)
                row.status = MissionRiskStatus.MITIGATED.value
                row.closed_at = utc_now()
                result["risk"] = self._risk_to_dict(row)
        return result

    def list_risk_assessments(
        self,
        risk_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_risk(session, risk_id)
            rows = list(
                session.scalars(
                    select(MissionRiskAssessmentModel)
                    .where(MissionRiskAssessmentModel.risk_id == risk_id)
                    .order_by(MissionRiskAssessmentModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._risk_assessment_to_dict(row) for row in rows]

    async def create_hypothesis(
        self,
        mission_id: str,
        request: MissionHypothesisCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if request.goal_id is not None:
                self._require_goal(session, mission_id, request.goal_id)
            row = MissionHypothesisModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=request.goal_id,
                hypothesis_key=request.hypothesis_key,
                statement=request.statement,
                rationale=request.rationale,
                status=MissionHypothesisStatus.PROPOSED.value,
                confidence_percent=request.confidence_percent,
                target_confidence_percent=request.target_confidence_percent,
                min_evidence_count=request.min_evidence_count,
                support_threshold_percent=request.support_threshold_percent,
                reject_threshold_percent=request.reject_threshold_percent,
                test_plan=request.test_plan,
                success_criteria=request.success_criteria,
                failure_criteria=request.failure_criteria,
                owner_id=request.owner_id,
                checkpoint_on_inconclusive=request.checkpoint_on_inconclusive,
                requires_human_decision=request.requires_human_decision,
                due_at=ensure_utc(request.due_at),
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AutonomousMissionError(
                    f"Hypothesis с ключом {request.hypothesis_key} уже существует."
                ) from exc
            result = self._hypothesis_to_dict(row)

        await self._publish(
            "mission.hypothesis.created",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"hypothesis": result},
        )
        return result

    def get_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionHypothesisModel, hypothesis_id)
            return None if row is None else self._hypothesis_to_dict(row)

    def list_hypotheses(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        goal_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionHypothesisModel).where(
                MissionHypothesisModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(MissionHypothesisModel.status == status)
            if goal_id is not None:
                statement = statement.where(MissionHypothesisModel.goal_id == goal_id)
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionHypothesisModel.updated_at.desc(),
                        MissionHypothesisModel.created_at.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._hypothesis_to_dict(row) for row in rows]

    async def update_hypothesis(
        self,
        hypothesis_id: str,
        request: MissionHypothesisUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_hypothesis(session, hypothesis_id)
            updates = request.model_dump(exclude_unset=True)
            if request.goal_id is not None:
                self._require_goal(session, row.mission_id, request.goal_id)
            for field, value in updates.items():
                if field == "metadata":
                    setattr(row, "metadata_json", dict(value or {}))
                elif field.endswith("_at"):
                    setattr(row, field, ensure_utc(value))
                else:
                    setattr(row, field, value)
            if (
                row.reject_threshold_percent
                >= row.support_threshold_percent
            ):
                raise AutonomousMissionError(
                    "reject_threshold_percent должен быть меньше support_threshold_percent."
                )
            row.version += 1
            result = self._hypothesis_to_dict(row)

        await self._publish(
            "mission.hypothesis.updated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"hypothesis": result},
        )
        return result

    async def evaluate_hypothesis(
        self,
        hypothesis_id: str,
        request: MissionHypothesisEvaluateRequest,
    ) -> dict[str, Any]:
        checkpoint: dict[str, Any] | None = None
        with self._session_factory() as session:
            row = self._require_hypothesis(session, hypothesis_id)
            evidence = self._load_hypothesis_evidence(
                session,
                row.mission_id,
                request.evidence_ids,
            )
            calculation = self._calculate_hypothesis_evidence(evidence)
            decision = request.decision
            if request.automatic:
                decision = self._automatic_hypothesis_decision(row, calculation)
            if decision is None:
                raise AutonomousMissionError("Не удалось определить решение Hypothesis.")
            previous_status = row.status
            new_status = self._hypothesis_status_for_decision(decision)
            confidence = (
                request.confidence_percent
                if request.confidence_percent is not None
                else float(calculation["confidence_percent"])
            )
            if not evidence and request.confidence_percent is None:
                confidence = row.confidence_percent
            row.status = new_status
            row.confidence_percent = round(float(confidence), 2)
            row.evidence_ids_json = [item.id for item in evidence]
            row.evaluated_at = utc_now()
            row.decided_at = (
                utc_now() if new_status in TERMINAL_HYPOTHESIS_STATUSES else None
            )
            row.version += 1
            evaluation = MissionHypothesisEvaluationModel(
                workspace_id=row.workspace_id,
                mission_id=row.mission_id,
                hypothesis_id=row.id,
                actor_id=request.actor_id,
                automatic=request.automatic,
                decision=decision.value,
                confidence_percent=row.confidence_percent,
                support_score_percent=float(calculation["support_score_percent"]),
                evidence_ids_json=[item.id for item in evidence],
                rationale=(
                    request.rationale
                    or self._automatic_hypothesis_rationale(decision, calculation)
                ),
                calculation_json=calculation,
                previous_status=previous_status,
                new_status=new_status,
            )
            session.add(evaluation)
            session.flush()
            if (
                request.create_checkpoint
                and row.checkpoint_on_inconclusive
                and new_status in {
                    MissionHypothesisStatus.REJECTED.value,
                    MissionHypothesisStatus.INCONCLUSIVE.value,
                    MissionHypothesisStatus.INVALIDATED.value,
                }
            ):
                cp = self._ensure_hypothesis_checkpoint(session, row)
                checkpoint = self._checkpoint_to_dict(cp)
            elif new_status == MissionHypothesisStatus.SUPPORTED.value:
                self._resolve_linked_checkpoint(
                    session,
                    hypothesis_id=row.id,
                    actor_id=request.actor_id,
                    rationale=(request.rationale or "Hypothesis supported."),
                    decision=MissionCheckpointDecision.CONTINUE.value,
                    automatic=request.automatic,
                )
            result = self._hypothesis_to_dict(row)
            evaluation_result = self._hypothesis_evaluation_to_dict(evaluation)

        self._hypothesis_evaluations += 1
        await self._publish(
            "mission.hypothesis.evaluated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={
                "hypothesis": result,
                "evaluation": evaluation_result,
                "checkpoint": checkpoint,
            },
        )
        return {
            "hypothesis": result,
            "evaluation": evaluation_result,
            "checkpoint": checkpoint,
        }

    async def reopen_hypothesis(
        self,
        hypothesis_id: str,
        request: MissionHypothesisReopenRequest,
    ) -> dict[str, Any]:
        return await self.evaluate_hypothesis(
            hypothesis_id,
            MissionHypothesisEvaluateRequest(
                actor_id=request.actor_id,
                automatic=False,
                decision=MissionHypothesisDecision.REOPEN,
                confidence_percent=request.confidence_percent,
                rationale=request.rationale,
                create_checkpoint=False,
            ),
        )

    def list_hypothesis_evaluations(
        self,
        hypothesis_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_hypothesis(session, hypothesis_id)
            rows = list(
                session.scalars(
                    select(MissionHypothesisEvaluationModel)
                    .where(
                        MissionHypothesisEvaluationModel.hypothesis_id
                        == hypothesis_id
                    )
                    .order_by(
                        MissionHypothesisEvaluationModel.created_at.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._hypothesis_evaluation_to_dict(row) for row in rows]

    async def create_checkpoint(
        self,
        mission_id: str,
        request: MissionCheckpointCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            self._validate_checkpoint_links(session, mission_id, request)
            now = utc_now()
            due_at = ensure_utc(request.due_at)
            status = (
                MissionCheckpointStatus.READY.value
                if due_at is None or due_at <= now
                else MissionCheckpointStatus.PENDING.value
            )
            row = MissionDecisionCheckpointModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=request.goal_id,
                cycle_id=request.cycle_id,
                risk_id=request.risk_id,
                hypothesis_id=request.hypothesis_id,
                checkpoint_key=request.checkpoint_key,
                checkpoint_type=request.checkpoint_type.value,
                title=request.title,
                description=request.description,
                status=status,
                blocking=request.blocking,
                requires_human=request.requires_human,
                auto_decision_enabled=request.auto_decision_enabled,
                auto_decision_threshold_percent=(
                    request.auto_decision_threshold_percent
                ),
                recommended_decision=(
                    None
                    if request.recommended_decision is None
                    else request.recommended_decision.value
                ),
                recommendation_confidence_percent=(
                    request.recommendation_confidence_percent
                ),
                due_at=due_at,
                expires_at=ensure_utc(request.expires_at),
                triggered_at=now if status == MissionCheckpointStatus.READY.value else None,
                trigger_conditions_json=dict(request.trigger_conditions),
                options_json=[dict(item) for item in request.options],
                context_snapshot_json=dict(request.context),
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AutonomousMissionError(
                    f"Checkpoint с ключом {request.checkpoint_key} уже существует."
                ) from exc
            result = self._checkpoint_to_dict(row)

        await self._publish(
            "mission.checkpoint.created",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"checkpoint": result},
        )
        return result

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionDecisionCheckpointModel, checkpoint_id)
            return None if row is None else self._checkpoint_to_dict(row)

    def list_checkpoints(
        self,
        mission_id: str,
        *,
        status: str | None = None,
        checkpoint_type: str | None = None,
        blocking: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            self._scan_checkpoints_in_session(session, mission_id)
            statement = select(MissionDecisionCheckpointModel).where(
                MissionDecisionCheckpointModel.mission_id == mission_id
            )
            if status is not None:
                statement = statement.where(
                    MissionDecisionCheckpointModel.status == status
                )
            if checkpoint_type is not None:
                statement = statement.where(
                    MissionDecisionCheckpointModel.checkpoint_type
                    == checkpoint_type
                )
            if blocking is not None:
                statement = statement.where(
                    MissionDecisionCheckpointModel.blocking.is_(blocking)
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionDecisionCheckpointModel.blocking.desc(),
                        MissionDecisionCheckpointModel.due_at.asc(),
                        MissionDecisionCheckpointModel.created_at.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._checkpoint_to_dict(row) for row in rows]

    async def scan_checkpoints(
        self,
        mission_id: str,
        *,
        auto_decide: bool = False,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        changed: list[dict[str, Any]] = []
        auto_candidates: list[str] = []
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            changed_rows = self._scan_checkpoints_in_session(session, mission_id)
            changed = [self._checkpoint_to_dict(row) for row in changed_rows]
            if auto_decide:
                auto_candidates = [
                    row.id
                    for row in session.scalars(
                        select(MissionDecisionCheckpointModel).where(
                            MissionDecisionCheckpointModel.mission_id == mission_id,
                            MissionDecisionCheckpointModel.status
                            == MissionCheckpointStatus.READY.value,
                            MissionDecisionCheckpointModel.auto_decision_enabled.is_(True),
                            MissionDecisionCheckpointModel.requires_human.is_(False),
                        )
                    ).all()
                ]
            workspace_id = mission.workspace_id

        decisions: list[dict[str, Any]] = []
        for checkpoint_id in auto_candidates:
            evaluated = await self.evaluate_checkpoint(
                checkpoint_id,
                MissionCheckpointEvaluateRequest(
                    actor_id=actor_id,
                    auto_decide=True,
                ),
            )
            if evaluated.get("decision") is not None:
                decisions.append(evaluated["decision"])
        await self._publish(
            "mission.checkpoint.scanned",
            workspace_id=workspace_id,
            mission_id=mission_id,
            payload={
                "changed": changed,
                "automatic_decisions": decisions,
            },
        )
        return {
            "mission_id": mission_id,
            "changed": changed,
            "automatic_decisions": decisions,
        }

    async def evaluate_checkpoint(
        self,
        checkpoint_id: str,
        request: MissionCheckpointEvaluateRequest,
    ) -> dict[str, Any]:
        auto_request: MissionCheckpointDecisionRequest | None = None
        with self._session_factory() as session:
            row = self._require_checkpoint(session, checkpoint_id)
            self._scan_checkpoint_row(session, row)
            recommendation, confidence, rationale, evaluation = (
                self._checkpoint_recommendation(session, row)
            )
            row.recommended_decision = recommendation
            row.recommendation_confidence_percent = confidence
            row.context_snapshot_json = {
                **dict(row.context_snapshot_json or {}),
                **dict(request.context),
                "last_evaluation": evaluation,
            }
            row.version += 1
            result = self._checkpoint_to_dict(row)
            if (
                request.auto_decide
                and row.status == MissionCheckpointStatus.READY.value
                and row.auto_decision_enabled
                and not row.requires_human
                and confidence >= row.auto_decision_threshold_percent
                and recommendation in {
                    MissionCheckpointDecision.CONTINUE.value,
                    MissionCheckpointDecision.ACCEPT_RISK.value,
                }
            ):
                auto_request = MissionCheckpointDecisionRequest(
                    decision=MissionCheckpointDecision(recommendation),
                    actor_id=request.actor_id,
                    rationale=rationale,
                    metadata={"automatic_checkpoint_evaluation": evaluation},
                )
            workspace_id = row.workspace_id
            mission_id = row.mission_id

        await self._publish(
            "mission.checkpoint.evaluated",
            workspace_id=workspace_id,
            mission_id=mission_id,
            payload={"checkpoint": result, "evaluation": evaluation},
        )
        decision_result = None
        if auto_request is not None:
            decision_result = await self.decide_checkpoint(
                checkpoint_id,
                auto_request,
                automatic=True,
            )
            self._automatic_decisions += 1
        return {
            "checkpoint": result,
            "evaluation": evaluation,
            "decision": decision_result,
        }

    async def decide_checkpoint(
        self,
        checkpoint_id: str,
        request: MissionCheckpointDecisionRequest,
        *,
        automatic: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_checkpoint(session, checkpoint_id)
            self._scan_checkpoint_row(session, row)
            if row.status not in {
                MissionCheckpointStatus.READY.value,
                MissionCheckpointStatus.DEFERRED.value,
            }:
                raise MissionStateError(
                    f"Checkpoint {checkpoint_id} нельзя решить в статусе {row.status}."
                )
            if automatic and row.requires_human:
                raise MissionStateError("Checkpoint требует решения пользователя.")
            previous_status = row.status
            new_status = MissionCheckpointStatus.RESOLVED.value
            now = utc_now()
            if request.decision == MissionCheckpointDecision.DEFER:
                defer_until = ensure_utc(request.defer_until)
                if defer_until is None or defer_until <= now:
                    raise AutonomousMissionError(
                        "defer_until должен быть позже текущего времени."
                    )
                new_status = MissionCheckpointStatus.DEFERRED.value
                row.due_at = defer_until
                row.triggered_at = None
                row.resolved_at = None
            elif request.decision == MissionCheckpointDecision.REQUEST_REVIEW:
                new_status = MissionCheckpointStatus.READY.value
                row.requires_human = True
                row.auto_decision_enabled = False
                row.resolved_at = None
            else:
                row.resolved_at = now
            row.status = new_status
            row.decision = request.decision.value
            row.decision_rationale = request.rationale
            row.decided_by = request.actor_id
            row.selected_option = request.selected_option
            row.version += 1
            mission = self._require_mission(session, row.mission_id)
            self._apply_checkpoint_decision(session, mission, row, request)
            decision_row = MissionCheckpointDecisionModel(
                workspace_id=row.workspace_id,
                mission_id=row.mission_id,
                checkpoint_id=row.id,
                actor_id=request.actor_id,
                automatic=automatic,
                decision=request.decision.value,
                rationale=request.rationale,
                selected_option=request.selected_option,
                previous_status=previous_status,
                new_status=new_status,
                context_json=dict(row.context_snapshot_json or {}),
                metadata_json=dict(request.metadata),
            )
            session.add(decision_row)
            session.flush()
            checkpoint_result = self._checkpoint_to_dict(row)
            decision_result = self._checkpoint_decision_to_dict(decision_row)
            mission_result = self._mission_to_dict(mission)

        await self._publish(
            "mission.checkpoint.decided",
            workspace_id=checkpoint_result["workspace_id"],
            mission_id=checkpoint_result["mission_id"],
            payload={
                "checkpoint": checkpoint_result,
                "decision": decision_result,
                "mission": mission_result,
            },
        )
        return {
            "checkpoint": checkpoint_result,
            "decision": decision_result,
            "mission": mission_result,
        }

    async def cancel_checkpoint(
        self,
        checkpoint_id: str,
        request: MissionCheckpointCancelRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_checkpoint(session, checkpoint_id)
            if row.status in {
                MissionCheckpointStatus.RESOLVED.value,
                MissionCheckpointStatus.CANCELLED.value,
                MissionCheckpointStatus.EXPIRED.value,
            }:
                raise MissionStateError(
                    f"Checkpoint уже находится в терминальном статусе {row.status}."
                )
            row.status = MissionCheckpointStatus.CANCELLED.value
            row.decided_by = request.actor_id
            row.decision_rationale = request.rationale
            row.resolved_at = utc_now()
            row.version += 1
            result = self._checkpoint_to_dict(row)
        await self._publish(
            "mission.checkpoint.cancelled",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={
                "checkpoint": result,
                "actor_id": request.actor_id,
                "rationale": request.rationale,
            },
        )
        return result

    def list_checkpoint_decisions(
        self,
        checkpoint_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_checkpoint(session, checkpoint_id)
            rows = list(
                session.scalars(
                    select(MissionCheckpointDecisionModel)
                    .where(
                        MissionCheckpointDecisionModel.checkpoint_id
                        == checkpoint_id
                    )
                    .order_by(MissionCheckpointDecisionModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._checkpoint_decision_to_dict(row) for row in rows]

    def evaluate_cycle_admission(
        self,
        cycle_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            cycle = session.get(MissionCycleModel, cycle_id)
            if cycle is None:
                raise MissionCycleNotFound(f"Mission Cycle {cycle_id} не найден.")
            mission = self._require_mission(session, cycle.mission_id)
            self._ensure_required_risk_checkpoints(session, mission.id)
            self._scan_checkpoints_in_session(session, mission.id)
            blockers = list(
                session.scalars(
                    select(MissionDecisionCheckpointModel).where(
                        MissionDecisionCheckpointModel.mission_id == mission.id,
                        MissionDecisionCheckpointModel.blocking.is_(True),
                        MissionDecisionCheckpointModel.status
                        == MissionCheckpointStatus.READY.value,
                    )
                ).all()
            )
            allowed = force or not blockers
            result = {
                "allowed": allowed,
                "forced": force,
                "mission_id": mission.id,
                "cycle_id": cycle.id,
                "blocking_checkpoint_count": len(blockers),
                "blocking_checkpoints": [
                    self._checkpoint_to_dict(row) for row in blockers
                ],
            }
        if not allowed:
            self._blocked_cycles += 1
        return result

    def build_context(
        self,
        mission_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            self._ensure_required_risk_checkpoints(session, mission_id)
            self._scan_checkpoints_in_session(session, mission_id)
            risks = list(
                session.scalars(
                    select(MissionRiskModel)
                    .where(
                        MissionRiskModel.mission_id == mission_id,
                        MissionRiskModel.status.in_(OPEN_RISK_STATUSES),
                    )
                    .order_by(MissionRiskModel.exposure_score.desc())
                    .limit(limit)
                ).all()
            )
            hypotheses = list(
                session.scalars(
                    select(MissionHypothesisModel)
                    .where(
                        MissionHypothesisModel.mission_id == mission_id,
                        MissionHypothesisModel.status.notin_([
                            MissionHypothesisStatus.INVALIDATED.value,
                        ]),
                    )
                    .order_by(MissionHypothesisModel.updated_at.desc())
                    .limit(limit)
                ).all()
            )
            checkpoints = list(
                session.scalars(
                    select(MissionDecisionCheckpointModel)
                    .where(
                        MissionDecisionCheckpointModel.mission_id == mission_id,
                        MissionDecisionCheckpointModel.status.in_(
                            OPEN_CHECKPOINT_STATUSES
                        ),
                    )
                    .order_by(
                        MissionDecisionCheckpointModel.blocking.desc(),
                        MissionDecisionCheckpointModel.due_at.asc(),
                    )
                    .limit(limit)
                ).all()
            )
            critical_risks = sum(
                1
                for risk in risks
                if risk.severity == MissionRiskSeverity.CRITICAL.value
            )
            blocking_checkpoints = sum(
                1
                for checkpoint in checkpoints
                if checkpoint.blocking
                and checkpoint.status == MissionCheckpointStatus.READY.value
            )
            context = {
                "mission_id": mission_id,
                "workspace_id": mission.workspace_id,
                "summary": {
                    "open_risks": len(risks),
                    "critical_risks": critical_risks,
                    "hypotheses": len(hypotheses),
                    "open_checkpoints": len(checkpoints),
                    "blocking_checkpoints": blocking_checkpoints,
                    "cycle_admission_allowed": blocking_checkpoints == 0,
                },
                "risks": [self._risk_to_dict(row) for row in risks],
                "hypotheses": [
                    self._hypothesis_to_dict(row) for row in hypotheses
                ],
                "checkpoints": [
                    self._checkpoint_to_dict(row) for row in checkpoints
                ],
            }
            return context

    def dashboard(self, mission_id: str) -> dict[str, Any]:
        context = self.build_context(mission_id, limit=200)
        with self._session_factory() as session:
            recent_assessments = list(
                session.scalars(
                    select(MissionRiskAssessmentModel)
                    .where(MissionRiskAssessmentModel.mission_id == mission_id)
                    .order_by(MissionRiskAssessmentModel.created_at.desc())
                    .limit(20)
                ).all()
            )
            recent_decisions = list(
                session.scalars(
                    select(MissionCheckpointDecisionModel)
                    .where(MissionCheckpointDecisionModel.mission_id == mission_id)
                    .order_by(MissionCheckpointDecisionModel.created_at.desc())
                    .limit(20)
                ).all()
            )
        return {
            **context,
            "recent_risk_assessments": [
                self._risk_assessment_to_dict(row) for row in recent_assessments
            ],
            "recent_checkpoint_decisions": [
                self._checkpoint_decision_to_dict(row) for row in recent_decisions
            ],
        }

    async def handle_event(self, event: Event) -> None:
        if event.event_type != "mission.cycle.failed":
            return
        payload = dict(event.payload or {})
        mission_id = payload.get("mission_id") or event.correlation_id
        cycle_id = payload.get("cycle_id")
        if not mission_id or not cycle_id:
            return
        risk_key = f"cycle_failure.{cycle_id}"
        with self._session_factory() as session:
            mission = session.get(WorkspaceMissionModel, mission_id)
            if mission is None:
                return
            existing = session.scalar(
                select(MissionRiskModel).where(
                    MissionRiskModel.mission_id == mission_id,
                    MissionRiskModel.risk_key == risk_key,
                )
            )
        error = str(payload.get("error") or "Mission cycle failed.")
        if existing is None:
            await self.create_risk(
                mission_id,
                MissionRiskCreate(
                    risk_key=risk_key,
                    title=f"Failure of Mission Cycle {cycle_id}",
                    description=error,
                    category=MissionRiskCategory.OPERATIONAL,
                    probability_percent=100,
                    impact_percent=70,
                    owner_id="system",
                    mitigation_plan="Investigate the failure and replan the Mission cycle.",
                    contingency_plan="Pause autonomous execution and request a decision.",
                    trigger_indicators=["mission.cycle.failed"],
                    checkpoint_required=True,
                    requires_human_decision=True,
                    metadata={"cycle_id": cycle_id, "event_id": event.id},
                ),
            )
        else:
            await self.materialize_risk(
                existing.id,
                MissionRiskMaterializeRequest(
                    actor_id="system",
                    rationale=error,
                    impact_percent=max(existing.impact_percent, 70),
                    indicators={"cycle_id": cycle_id, "event_id": event.id},
                    create_checkpoint=True,
                ),
            )
        self._captured_failures += 1

    def _ensure_required_risk_checkpoints(
        self,
        session: Session,
        mission_id: str,
    ) -> list[MissionDecisionCheckpointModel]:
        created: list[MissionDecisionCheckpointModel] = []
        risks = list(
            session.scalars(
                select(MissionRiskModel).where(
                    MissionRiskModel.mission_id == mission_id,
                    MissionRiskModel.checkpoint_required.is_(True),
                    MissionRiskModel.status.in_(OPEN_RISK_STATUSES),
                    MissionRiskModel.severity.in_([
                        MissionRiskSeverity.HIGH.value,
                        MissionRiskSeverity.CRITICAL.value,
                    ]),
                )
            ).all()
        )
        for risk in risks:
            checkpoint = self._ensure_risk_checkpoint(session, risk)
            created.append(checkpoint)
        return created

    def _ensure_risk_checkpoint(
        self,
        session: Session,
        risk: MissionRiskModel,
    ) -> MissionDecisionCheckpointModel:
        checkpoint_key = f"risk.{risk.risk_key}"
        row = session.scalar(
            select(MissionDecisionCheckpointModel).where(
                MissionDecisionCheckpointModel.mission_id == risk.mission_id,
                MissionDecisionCheckpointModel.checkpoint_key == checkpoint_key,
            )
        )
        recommendation = (
            MissionCheckpointDecision.PAUSE.value
            if risk.severity == MissionRiskSeverity.CRITICAL.value
            or risk.status == MissionRiskStatus.MATERIALIZED.value
            else MissionCheckpointDecision.REQUEST_REVIEW.value
        )
        confidence = max(50.0, float(risk.exposure_score))
        if row is None:
            row = MissionDecisionCheckpointModel(
                workspace_id=risk.workspace_id,
                mission_id=risk.mission_id,
                goal_id=risk.goal_id,
                risk_id=risk.id,
                checkpoint_key=checkpoint_key,
                checkpoint_type=MissionCheckpointType.RISK.value,
                title=f"Risk decision: {risk.title}",
                description=(
                    "A high or critical Mission risk requires an explicit decision."
                ),
                status=MissionCheckpointStatus.READY.value,
                blocking=True,
                requires_human=risk.requires_human_decision,
                auto_decision_enabled=False,
                recommended_decision=recommendation,
                recommendation_confidence_percent=confidence,
                triggered_at=utc_now(),
                trigger_conditions_json={
                    "risk_severity": ["high", "critical"],
                    "risk_status": list(OPEN_RISK_STATUSES),
                },
                context_snapshot_json={"risk": self._risk_to_dict(risk)},
                metadata_json={"generated_by": "mission_risk_register"},
            )
            session.add(row)
            session.flush()
            return row
        if row.status in {
            MissionCheckpointStatus.RESOLVED.value,
            MissionCheckpointStatus.CANCELLED.value,
            MissionCheckpointStatus.EXPIRED.value,
        }:
            row.status = MissionCheckpointStatus.READY.value
            row.triggered_at = utc_now()
            row.resolved_at = None
            row.decision = None
            row.decision_rationale = None
            row.decided_by = None
        row.goal_id = risk.goal_id
        row.risk_id = risk.id
        row.blocking = True
        row.requires_human = risk.requires_human_decision
        row.recommended_decision = recommendation
        row.recommendation_confidence_percent = confidence
        row.context_snapshot_json = {"risk": self._risk_to_dict(risk)}
        row.version += 1
        return row

    def _ensure_hypothesis_checkpoint(
        self,
        session: Session,
        hypothesis: MissionHypothesisModel,
    ) -> MissionDecisionCheckpointModel:
        checkpoint_key = f"hypothesis.{hypothesis.hypothesis_key}"
        row = session.scalar(
            select(MissionDecisionCheckpointModel).where(
                MissionDecisionCheckpointModel.mission_id
                == hypothesis.mission_id,
                MissionDecisionCheckpointModel.checkpoint_key == checkpoint_key,
            )
        )
        recommendation = (
            MissionCheckpointDecision.REPLAN.value
            if hypothesis.status
            in {
                MissionHypothesisStatus.REJECTED.value,
                MissionHypothesisStatus.INVALIDATED.value,
            }
            else MissionCheckpointDecision.REQUEST_REVIEW.value
        )
        confidence = max(50.0, float(hypothesis.confidence_percent))
        if row is None:
            row = MissionDecisionCheckpointModel(
                workspace_id=hypothesis.workspace_id,
                mission_id=hypothesis.mission_id,
                goal_id=hypothesis.goal_id,
                hypothesis_id=hypothesis.id,
                checkpoint_key=checkpoint_key,
                checkpoint_type=MissionCheckpointType.HYPOTHESIS.value,
                title=f"Hypothesis decision: {hypothesis.hypothesis_key}",
                description=hypothesis.statement,
                status=MissionCheckpointStatus.READY.value,
                blocking=True,
                requires_human=hypothesis.requires_human_decision,
                auto_decision_enabled=False,
                recommended_decision=recommendation,
                recommendation_confidence_percent=confidence,
                triggered_at=utc_now(),
                trigger_conditions_json={
                    "hypothesis_status": [
                        "rejected",
                        "inconclusive",
                        "invalidated",
                    ]
                },
                context_snapshot_json={
                    "hypothesis": self._hypothesis_to_dict(hypothesis)
                },
                metadata_json={"generated_by": "mission_hypothesis_register"},
            )
            session.add(row)
            session.flush()
            return row
        if row.status in {
            MissionCheckpointStatus.RESOLVED.value,
            MissionCheckpointStatus.CANCELLED.value,
            MissionCheckpointStatus.EXPIRED.value,
        }:
            row.status = MissionCheckpointStatus.READY.value
            row.triggered_at = utc_now()
            row.resolved_at = None
            row.decision = None
            row.decision_rationale = None
            row.decided_by = None
        row.hypothesis_id = hypothesis.id
        row.goal_id = hypothesis.goal_id
        row.blocking = True
        row.requires_human = hypothesis.requires_human_decision
        row.recommended_decision = recommendation
        row.recommendation_confidence_percent = confidence
        row.context_snapshot_json = {
            "hypothesis": self._hypothesis_to_dict(hypothesis)
        }
        row.version += 1
        return row

    def _resolve_linked_checkpoint(
        self,
        session: Session,
        *,
        risk_id: str | None = None,
        hypothesis_id: str | None = None,
        actor_id: str,
        rationale: str,
        decision: str,
        automatic: bool,
    ) -> None:
        statement = select(MissionDecisionCheckpointModel).where(
            MissionDecisionCheckpointModel.status.in_(OPEN_CHECKPOINT_STATUSES)
        )
        if risk_id is not None:
            statement = statement.where(
                MissionDecisionCheckpointModel.risk_id == risk_id
            )
        elif hypothesis_id is not None:
            statement = statement.where(
                MissionDecisionCheckpointModel.hypothesis_id == hypothesis_id
            )
        else:
            return
        rows = list(session.scalars(statement).all())
        for row in rows:
            previous_status = row.status
            row.status = MissionCheckpointStatus.RESOLVED.value
            row.decision = decision
            row.decision_rationale = rationale
            row.decided_by = actor_id
            row.resolved_at = utc_now()
            row.version += 1
            session.add(
                MissionCheckpointDecisionModel(
                    workspace_id=row.workspace_id,
                    mission_id=row.mission_id,
                    checkpoint_id=row.id,
                    actor_id=actor_id,
                    automatic=automatic,
                    decision=decision,
                    rationale=rationale,
                    previous_status=previous_status,
                    new_status=MissionCheckpointStatus.RESOLVED.value,
                    context_json=dict(row.context_snapshot_json or {}),
                    metadata_json={"resolved_from_linked_entity": True},
                )
            )

    def _scan_checkpoints_in_session(
        self,
        session: Session,
        mission_id: str,
    ) -> list[MissionDecisionCheckpointModel]:
        rows = list(
            session.scalars(
                select(MissionDecisionCheckpointModel).where(
                    MissionDecisionCheckpointModel.mission_id == mission_id,
                    MissionDecisionCheckpointModel.status.in_(
                        OPEN_CHECKPOINT_STATUSES
                    ),
                )
            ).all()
        )
        changed: list[MissionDecisionCheckpointModel] = []
        for row in rows:
            if self._scan_checkpoint_row(session, row):
                changed.append(row)
        return changed

    def _scan_checkpoint_row(
        self,
        session: Session,
        row: MissionDecisionCheckpointModel,
    ) -> bool:
        now = utc_now()
        changed = False
        expires_at = ensure_utc(row.expires_at)
        due_at = ensure_utc(row.due_at)
        if (
            expires_at is not None
            and expires_at <= now
            and row.status not in {
                MissionCheckpointStatus.RESOLVED.value,
                MissionCheckpointStatus.CANCELLED.value,
                MissionCheckpointStatus.EXPIRED.value,
            }
        ):
            row.status = MissionCheckpointStatus.EXPIRED.value
            row.resolved_at = now
            row.version += 1
            return True
        should_ready = False
        if row.status in {
            MissionCheckpointStatus.PENDING.value,
            MissionCheckpointStatus.DEFERRED.value,
        } and (due_at is None or due_at <= now):
            should_ready = True
        if row.risk_id is not None:
            risk = session.get(MissionRiskModel, row.risk_id)
            if (
                risk is not None
                and risk.status in OPEN_RISK_STATUSES
                and risk.severity in {
                    MissionRiskSeverity.HIGH.value,
                    MissionRiskSeverity.CRITICAL.value,
                }
            ):
                should_ready = True
        if row.hypothesis_id is not None:
            hypothesis = session.get(
                MissionHypothesisModel,
                row.hypothesis_id,
            )
            if (
                hypothesis is not None
                and hypothesis.status
                in {
                    MissionHypothesisStatus.REJECTED.value,
                    MissionHypothesisStatus.INCONCLUSIVE.value,
                    MissionHypothesisStatus.INVALIDATED.value,
                }
            ):
                should_ready = True
        if should_ready and row.status != MissionCheckpointStatus.READY.value:
            row.status = MissionCheckpointStatus.READY.value
            row.triggered_at = row.triggered_at or now
            row.version += 1
            changed = True
        return changed

    def _checkpoint_recommendation(
        self,
        session: Session,
        row: MissionDecisionCheckpointModel,
    ) -> tuple[str, float, str, dict[str, Any]]:
        evaluation: dict[str, Any] = {
            "checkpoint_type": row.checkpoint_type,
            "evaluated_at": iso(utc_now()),
        }
        if row.risk_id is not None:
            risk = session.get(MissionRiskModel, row.risk_id)
            if risk is not None:
                evaluation["risk"] = self._risk_to_dict(risk)
                if risk.status == MissionRiskStatus.ACCEPTED.value:
                    return (
                        MissionCheckpointDecision.ACCEPT_RISK.value,
                        100.0,
                        "Risk was explicitly accepted.",
                        evaluation,
                    )
                if risk.status in {
                    MissionRiskStatus.MITIGATED.value,
                    MissionRiskStatus.CLOSED.value,
                }:
                    return (
                        MissionCheckpointDecision.CONTINUE.value,
                        95.0,
                        "Risk is mitigated or closed.",
                        evaluation,
                    )
                if risk.status == MissionRiskStatus.MATERIALIZED.value:
                    return (
                        MissionCheckpointDecision.PAUSE.value,
                        max(80.0, risk.exposure_score),
                        "Risk materialized and requires intervention.",
                        evaluation,
                    )
                if risk.severity == MissionRiskSeverity.CRITICAL.value:
                    return (
                        MissionCheckpointDecision.PAUSE.value,
                        max(80.0, risk.exposure_score),
                        "Critical risk requires a human decision.",
                        evaluation,
                    )
                return (
                    MissionCheckpointDecision.REQUEST_REVIEW.value,
                    max(50.0, risk.exposure_score),
                    "High risk requires review before continuing.",
                    evaluation,
                )
        if row.hypothesis_id is not None:
            hypothesis = session.get(
                MissionHypothesisModel,
                row.hypothesis_id,
            )
            if hypothesis is not None:
                evaluation["hypothesis"] = self._hypothesis_to_dict(hypothesis)
                if hypothesis.status == MissionHypothesisStatus.SUPPORTED.value:
                    return (
                        MissionCheckpointDecision.CONTINUE.value,
                        hypothesis.confidence_percent,
                        "Hypothesis is supported by evidence.",
                        evaluation,
                    )
                if hypothesis.status in {
                    MissionHypothesisStatus.REJECTED.value,
                    MissionHypothesisStatus.INVALIDATED.value,
                }:
                    return (
                        MissionCheckpointDecision.REPLAN.value,
                        hypothesis.confidence_percent,
                        "Hypothesis was rejected or invalidated.",
                        evaluation,
                    )
                return (
                    MissionCheckpointDecision.REQUEST_REVIEW.value,
                    max(50.0, hypothesis.confidence_percent),
                    "Hypothesis remains inconclusive.",
                    evaluation,
                )
        if row.checkpoint_type == MissionCheckpointType.DEADLINE.value:
            mission = self._require_mission(session, row.mission_id)
            deadline = ensure_utc(mission.deadline_at)
            evaluation["mission_deadline_at"] = iso(deadline)
            if deadline is not None and deadline <= utc_now():
                return (
                    MissionCheckpointDecision.PAUSE.value,
                    100.0,
                    "Mission deadline has passed.",
                    evaluation,
                )
        recommendation = (
            row.recommended_decision
            or MissionCheckpointDecision.REQUEST_REVIEW.value
        )
        confidence = float(row.recommendation_confidence_percent or 50.0)
        return recommendation, confidence, "Checkpoint requires review.", evaluation

    def _apply_checkpoint_decision(
        self,
        session: Session,
        mission: WorkspaceMissionModel,
        checkpoint: MissionDecisionCheckpointModel,
        request: MissionCheckpointDecisionRequest,
    ) -> None:
        now = utc_now()
        decision = request.decision
        if decision == MissionCheckpointDecision.PAUSE:
            if mission.status == MissionStatus.ACTIVE.value:
                mission.status = MissionStatus.PAUSED.value
                mission.pause_reason = request.rationale
                mission.last_activity_at = now
                mission.version += 1
        elif decision == MissionCheckpointDecision.CANCEL:
            if mission.status not in {
                MissionStatus.COMPLETED.value,
                MissionStatus.CANCELLED.value,
            }:
                mission.status = MissionStatus.CANCELLED.value
                mission.cancelled_at = now
                mission.last_activity_at = now
                mission.version += 1
        elif decision == MissionCheckpointDecision.REPLAN:
            mission.next_cycle_at = now
            mission.last_activity_at = now
            mission.version += 1
            metadata = dict(mission.metadata_json or {})
            metadata["replan_requested"] = {
                "checkpoint_id": checkpoint.id,
                "requested_at": iso(now),
                "actor_id": request.actor_id,
                "reason": request.rationale,
            }
            mission.metadata_json = metadata
        elif decision == MissionCheckpointDecision.ACCEPT_RISK:
            if checkpoint.risk_id is not None:
                risk = session.get(MissionRiskModel, checkpoint.risk_id)
                if risk is not None:
                    risk.status = MissionRiskStatus.ACCEPTED.value
                    risk.last_assessed_at = now
                    risk.version += 1
        elif decision == MissionCheckpointDecision.CONTINUE:
            mission.last_activity_at = now
            mission.version += 1

    @staticmethod
    def _risk_status_for_decision(
        decision: MissionRiskDecision,
        *,
        current: str,
    ) -> str:
        return {
            MissionRiskDecision.MONITOR: MissionRiskStatus.MONITORING.value,
            MissionRiskDecision.MITIGATE: MissionRiskStatus.MITIGATED.value,
            MissionRiskDecision.ACCEPT: MissionRiskStatus.ACCEPTED.value,
            MissionRiskDecision.ESCALATE: MissionRiskStatus.MONITORING.value,
            MissionRiskDecision.MATERIALIZE: MissionRiskStatus.MATERIALIZED.value,
            MissionRiskDecision.CLOSE: MissionRiskStatus.CLOSED.value,
        }.get(decision, current)

    @staticmethod
    def _hypothesis_status_for_decision(
        decision: MissionHypothesisDecision,
    ) -> str:
        return {
            MissionHypothesisDecision.SUPPORT: MissionHypothesisStatus.SUPPORTED.value,
            MissionHypothesisDecision.REJECT: MissionHypothesisStatus.REJECTED.value,
            MissionHypothesisDecision.INCONCLUSIVE: (
                MissionHypothesisStatus.INCONCLUSIVE.value
            ),
            MissionHypothesisDecision.INVALIDATE: (
                MissionHypothesisStatus.INVALIDATED.value
            ),
            MissionHypothesisDecision.REOPEN: MissionHypothesisStatus.TESTING.value,
        }[decision]

    def _load_hypothesis_evidence(
        self,
        session: Session,
        mission_id: str,
        evidence_ids: list[str],
    ) -> list[MissionEvidenceModel]:
        cleaned = list(dict.fromkeys(evidence_ids))
        if not cleaned:
            return []
        rows = list(
            session.scalars(
                select(MissionEvidenceModel).where(
                    MissionEvidenceModel.id.in_(cleaned)
                )
            ).all()
        )
        by_id = {row.id: row for row in rows}
        missing = [item for item in cleaned if item not in by_id]
        if missing:
            raise AutonomousMissionError(
                f"Evidence не найдены: {', '.join(missing)}"
            )
        if any(row.mission_id != mission_id for row in rows):
            raise AutonomousMissionError(
                "Все Evidence должны принадлежать той же Mission."
            )
        return [by_id[item] for item in cleaned]

    @staticmethod
    def _calculate_hypothesis_evidence(
        evidence: list[MissionEvidenceModel],
    ) -> dict[str, Any]:
        support_weight = 0.0
        reject_weight = 0.0
        neutral_weight = 0.0
        scores: list[float] = []
        effects: list[dict[str, Any]] = []
        for row in evidence:
            content = dict(row.content_json or {})
            effect = content.get("hypothesis_effect")
            if effect is None and "supports_hypothesis" in content:
                effect = "support" if bool(content["supports_hypothesis"]) else "reject"
            effect = str(effect or "inconclusive").lower()
            weight = max(1.0, float(row.aggregate_score or 0.0))
            scores.append(float(row.aggregate_score or 0.0))
            if effect in {"support", "supported", "confirm", "positive"}:
                support_weight += weight
                normalized = "support"
            elif effect in {"reject", "rejected", "contradict", "negative"}:
                reject_weight += weight
                normalized = "reject"
            else:
                neutral_weight += weight
                normalized = "inconclusive"
            effects.append(
                {
                    "evidence_id": row.id,
                    "effect": normalized,
                    "weight": round(weight, 2),
                    "aggregate_score": float(row.aggregate_score or 0.0),
                    "status": row.status,
                }
            )
        directional = support_weight + reject_weight
        support_score = (
            50.0
            if directional <= 0
            else round(100.0 * support_weight / directional, 2)
        )
        confidence = (
            0.0 if not scores else round(sum(scores) / len(scores), 2)
        )
        return {
            "evidence_count": len(evidence),
            "support_weight": round(support_weight, 2),
            "reject_weight": round(reject_weight, 2),
            "neutral_weight": round(neutral_weight, 2),
            "support_score_percent": support_score,
            "confidence_percent": confidence,
            "effects": effects,
        }

    @staticmethod
    def _automatic_hypothesis_decision(
        hypothesis: MissionHypothesisModel,
        calculation: dict[str, Any],
    ) -> MissionHypothesisDecision:
        count = int(calculation["evidence_count"])
        support = float(calculation["support_score_percent"])
        confidence = float(calculation["confidence_percent"])
        if count < hypothesis.min_evidence_count:
            return MissionHypothesisDecision.INCONCLUSIVE
        if (
            support >= hypothesis.support_threshold_percent
            and confidence >= hypothesis.target_confidence_percent
        ):
            return MissionHypothesisDecision.SUPPORT
        if support <= hypothesis.reject_threshold_percent:
            return MissionHypothesisDecision.REJECT
        return MissionHypothesisDecision.INCONCLUSIVE

    @staticmethod
    def _automatic_hypothesis_rationale(
        decision: MissionHypothesisDecision,
        calculation: dict[str, Any],
    ) -> str:
        return (
            f"Automatic evidence evaluation: decision={decision.value}, "
            f"evidence_count={calculation['evidence_count']}, "
            f"support={calculation['support_score_percent']}%, "
            f"confidence={calculation['confidence_percent']}%."
        )

    def _validate_checkpoint_links(
        self,
        session: Session,
        mission_id: str,
        request: MissionCheckpointCreate,
    ) -> None:
        if request.goal_id is not None:
            self._require_goal(session, mission_id, request.goal_id)
        if request.cycle_id is not None:
            cycle = session.get(MissionCycleModel, request.cycle_id)
            if cycle is None or cycle.mission_id != mission_id:
                raise MissionCycleNotFound(
                    f"Mission Cycle {request.cycle_id} не найден."
                )
        if request.risk_id is not None:
            risk = self._require_risk(session, request.risk_id)
            if risk.mission_id != mission_id:
                raise AutonomousMissionError("Risk принадлежит другой Mission.")
        if request.hypothesis_id is not None:
            hypothesis = self._require_hypothesis(
                session,
                request.hypothesis_id,
            )
            if hypothesis.mission_id != mission_id:
                raise AutonomousMissionError(
                    "Hypothesis принадлежит другой Mission."
                )

    def _require_mission(
        self,
        session: Session,
        mission_id: str,
    ) -> WorkspaceMissionModel:
        row = session.get(WorkspaceMissionModel, mission_id)
        if row is None:
            raise MissionNotFound(f"Mission {mission_id} не найдена.")
        return row

    def _require_goal(
        self,
        session: Session,
        mission_id: str,
        goal_id: str,
    ) -> MissionGoalModel:
        row = session.get(MissionGoalModel, goal_id)
        if row is None or row.mission_id != mission_id:
            raise MissionGoalNotFound(f"Mission Goal {goal_id} не найдена.")
        return row

    def _require_risk(
        self,
        session: Session,
        risk_id: str,
    ) -> MissionRiskModel:
        row = session.get(MissionRiskModel, risk_id)
        if row is None:
            raise AutonomousMissionError(f"Mission Risk {risk_id} не найден.")
        return row

    def _require_hypothesis(
        self,
        session: Session,
        hypothesis_id: str,
    ) -> MissionHypothesisModel:
        row = session.get(MissionHypothesisModel, hypothesis_id)
        if row is None:
            raise AutonomousMissionError(
                f"Mission Hypothesis {hypothesis_id} не найдена."
            )
        return row

    def _require_checkpoint(
        self,
        session: Session,
        checkpoint_id: str,
    ) -> MissionDecisionCheckpointModel:
        row = session.get(MissionDecisionCheckpointModel, checkpoint_id)
        if row is None:
            raise AutonomousMissionError(
                f"Mission Checkpoint {checkpoint_id} не найден."
            )
        return row

    @staticmethod
    def _clean_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @staticmethod
    def _risk_to_dict(row: MissionRiskModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "risk_key": row.risk_key,
            "title": row.title,
            "description": row.description,
            "category": row.category,
            "status": row.status,
            "probability_percent": float(row.probability_percent),
            "impact_percent": float(row.impact_percent),
            "exposure_score": float(row.exposure_score),
            "severity": row.severity,
            "owner_id": row.owner_id,
            "mitigation_plan": row.mitigation_plan,
            "contingency_plan": row.contingency_plan,
            "trigger_indicators": list(row.trigger_indicators_json or []),
            "checkpoint_required": row.checkpoint_required,
            "requires_human_decision": row.requires_human_decision,
            "due_at": iso(row.due_at),
            "next_review_at": iso(row.next_review_at),
            "last_assessed_at": iso(row.last_assessed_at),
            "materialized_at": iso(row.materialized_at),
            "closed_at": iso(row.closed_at),
            "version": row.version,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _risk_assessment_to_dict(
        row: MissionRiskAssessmentModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "risk_id": row.risk_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "decision": row.decision,
            "probability_percent": float(row.probability_percent),
            "impact_percent": float(row.impact_percent),
            "exposure_score": float(row.exposure_score),
            "severity": row.severity,
            "rationale": row.rationale,
            "indicators": dict(row.indicators_json or {}),
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _hypothesis_to_dict(row: MissionHypothesisModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "hypothesis_key": row.hypothesis_key,
            "statement": row.statement,
            "rationale": row.rationale,
            "status": row.status,
            "confidence_percent": float(row.confidence_percent),
            "target_confidence_percent": float(row.target_confidence_percent),
            "min_evidence_count": row.min_evidence_count,
            "support_threshold_percent": float(row.support_threshold_percent),
            "reject_threshold_percent": float(row.reject_threshold_percent),
            "test_plan": row.test_plan,
            "success_criteria": row.success_criteria,
            "failure_criteria": row.failure_criteria,
            "owner_id": row.owner_id,
            "checkpoint_on_inconclusive": row.checkpoint_on_inconclusive,
            "requires_human_decision": row.requires_human_decision,
            "due_at": iso(row.due_at),
            "evaluated_at": iso(row.evaluated_at),
            "decided_at": iso(row.decided_at),
            "evidence_ids": list(row.evidence_ids_json or []),
            "version": row.version,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _hypothesis_evaluation_to_dict(
        row: MissionHypothesisEvaluationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "hypothesis_id": row.hypothesis_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "decision": row.decision,
            "confidence_percent": float(row.confidence_percent),
            "support_score_percent": float(row.support_score_percent),
            "evidence_ids": list(row.evidence_ids_json or []),
            "rationale": row.rationale,
            "calculation": dict(row.calculation_json or {}),
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _checkpoint_to_dict(
        row: MissionDecisionCheckpointModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "cycle_id": row.cycle_id,
            "risk_id": row.risk_id,
            "hypothesis_id": row.hypothesis_id,
            "checkpoint_key": row.checkpoint_key,
            "checkpoint_type": row.checkpoint_type,
            "title": row.title,
            "description": row.description,
            "status": row.status,
            "blocking": row.blocking,
            "requires_human": row.requires_human,
            "auto_decision_enabled": row.auto_decision_enabled,
            "auto_decision_threshold_percent": float(
                row.auto_decision_threshold_percent
            ),
            "recommended_decision": row.recommended_decision,
            "recommendation_confidence_percent": float(
                row.recommendation_confidence_percent
            ),
            "due_at": iso(row.due_at),
            "expires_at": iso(row.expires_at),
            "triggered_at": iso(row.triggered_at),
            "resolved_at": iso(row.resolved_at),
            "decided_by": row.decided_by,
            "decision": row.decision,
            "decision_rationale": row.decision_rationale,
            "selected_option": row.selected_option,
            "trigger_conditions": dict(row.trigger_conditions_json or {}),
            "options": list(row.options_json or []),
            "context": dict(row.context_snapshot_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "version": row.version,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _checkpoint_decision_to_dict(
        row: MissionCheckpointDecisionModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "checkpoint_id": row.checkpoint_id,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "decision": row.decision,
            "rationale": row.rationale,
            "selected_option": row.selected_option,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "context": dict(row.context_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _mission_to_dict(row: WorkspaceMissionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "status": row.status,
            "progress_percent": float(row.progress_percent),
            "next_cycle_at": iso(row.next_cycle_at),
            "last_activity_at": iso(row.last_activity_at),
            "version": row.version,
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
                source="mission_governance_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, **payload},
            )
        )
