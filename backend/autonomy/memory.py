from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.autonomy.memory_schemas import (
    MissionEvidenceEvaluateRequest,
    MissionEvidencePolicyUpsert,
    MissionEvidenceReviewRequest,
    MissionEvidenceStatus,
    MissionEvidenceSubmit,
    MissionEvidenceType,
    MissionGoalConfirmRequest,
    MissionGoalConfirmationDecision,
    MissionGoalEvidenceEvaluateRequest,
    MissionGoalReopenRequest,
    MissionMemoryUpsert,
)
from backend.autonomy.models import (
    MissionCycleModel,
    MissionEvidenceModel,
    MissionEvidencePolicyModel,
    MissionGoalConfirmationModel,
    MissionGoalModel,
    MissionMemoryEntryModel,
    MissionProgressUpdateModel,
    WorkspaceMissionModel,
)
from backend.autonomy.schemas import MissionGoalStatus, MissionStatus
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


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]{3,}")
_STOP_WORDS = {
    "для", "или", "как", "при", "это", "that", "with", "from", "this",
    "the", "and", "goal", "mission", "результат", "цель",
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


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_hash(*, evidence_type: str, source_ref: str | None, content: Any) -> str:
    payload = {
        "evidence_type": evidence_type,
        "source_ref": source_ref,
        "content": content,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class MissionMemoryService:
    """Mission-scoped memory, evidence scoring and goal confirmation.

    Automatic goal confirmation is opt-in. The default policy requires human
    review and therefore cannot complete a goal without an explicit decision.
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
        self._automatic_confirmations = 0
        self._execution_evidence_captured = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            memory_count = int(
                session.scalar(
                    select(func.count()).select_from(MissionMemoryEntryModel)
                )
                or 0
            )
            evidence_count = int(
                session.scalar(
                    select(func.count()).select_from(MissionEvidenceModel)
                )
                or 0
            )
            accepted_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(MissionEvidenceModel)
                    .where(MissionEvidenceModel.status == MissionEvidenceStatus.ACCEPTED.value)
                )
                or 0
            )
            confirmation_count = int(
                session.scalar(
                    select(func.count()).select_from(MissionGoalConfirmationModel)
                )
                or 0
            )
        return {
            "memory_entries": memory_count,
            "evidence": evidence_count,
            "accepted_evidence": accepted_count,
            "goal_confirmations": confirmation_count,
            "evaluations": self._evaluations,
            "automatic_confirmations": self._automatic_confirmations,
            "execution_evidence_captured": self._execution_evidence_captured,
            "default_policy": {
                "require_human_review": True,
                "auto_confirm_enabled": False,
            },
            "capabilities": [
                "mission_scoped_memory",
                "versioned_memory_entries",
                "evidence_deduplication",
                "deterministic_evidence_scoring",
                "human_evidence_review",
                "goal_evidence_policy",
                "automatic_goal_confirmation",
                "execution_result_capture",
            ],
        }

    async def upsert_memory(
        self,
        mission_id: str,
        memory_key: str,
        request: MissionMemoryUpsert,
    ) -> dict[str, Any]:
        normalized_key = memory_key.strip()
        if not normalized_key or len(normalized_key) > 255:
            raise AutonomousMissionError("memory_key должен содержать от 1 до 255 символов.")
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            if request.goal_id is not None:
                self._require_goal(session, mission_id, request.goal_id)
            row = session.scalar(
                select(MissionMemoryEntryModel).where(
                    MissionMemoryEntryModel.mission_id == mission_id,
                    MissionMemoryEntryModel.memory_key == normalized_key,
                )
            )
            created = row is None
            if row is None:
                row = MissionMemoryEntryModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission_id,
                    memory_key=normalized_key,
                )
                session.add(row)
            else:
                row.version += 1
            row.goal_id = request.goal_id
            row.category = request.category.value
            row.content_json = dict(request.content)
            row.source_type = request.source_type
            row.source_ref = request.source_ref
            row.importance_score = request.importance_score
            row.confidence_score = request.confidence_score
            row.expires_at = ensure_utc(request.expires_at)
            row.created_by = request.created_by
            row.archived = False
            session.flush()
            result = self._memory_to_dict(row)

        await self._publish(
            "mission.memory.created" if created else "mission.memory.updated",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"memory": result},
        )
        return result

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionMemoryEntryModel, memory_id)
            return None if row is None else self._memory_to_dict(row)

    def list_memory(
        self,
        mission_id: str,
        *,
        goal_id: str | None = None,
        category: str | None = None,
        query: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionMemoryEntryModel).where(
                MissionMemoryEntryModel.mission_id == mission_id
            )
            if goal_id is not None:
                statement = statement.where(MissionMemoryEntryModel.goal_id == goal_id)
            if category is not None:
                statement = statement.where(MissionMemoryEntryModel.category == category)
            if not include_archived:
                statement = statement.where(MissionMemoryEntryModel.archived.is_(False))
            rows = list(
                session.scalars(
                    statement.order_by(
                        MissionMemoryEntryModel.importance_score.desc(),
                        MissionMemoryEntryModel.updated_at.desc(),
                    )
                ).all()
            )
            now = utc_now()
            rows = [
                row
                for row in rows
                if row.expires_at is None or ensure_utc(row.expires_at) > now
            ]
            if query:
                needle = query.casefold()
                rows = [
                    row for row in rows
                    if needle in (
                        f"{row.memory_key} {row.source_ref or ''} "
                        f"{canonical_json(row.content_json or {})}"
                    ).casefold()
                ]
            return [
                self._memory_to_dict(row)
                for row in rows[offset: offset + limit]
            ]

    def build_context(
        self,
        mission_id: str,
        *,
        goal_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        entries = self.list_memory(
            mission_id,
            goal_id=goal_id,
            include_archived=False,
            limit=limit,
        )
        return {
            "mission_id": mission_id,
            "goal_id": goal_id,
            "entry_count": len(entries),
            "context": {entry["memory_key"]: entry["content"] for entry in entries},
            "entries": entries,
        }

    async def archive_memory(
        self,
        mission_id: str,
        memory_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(MissionMemoryEntryModel, memory_id)
            if row is None or row.mission_id != mission_id:
                raise AutonomousMissionError("Mission Memory entry не найдена.")
            row.archived = True
            row.version += 1
            result = self._memory_to_dict(row)
        await self._publish(
            "mission.memory.archived",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"memory_id": memory_id, "actor_id": actor_id, "reason": reason},
        )
        return result

    def get_evidence_policy(self, mission_id: str, goal_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            self._require_goal(session, mission_id, goal_id)
            row = session.scalar(
                select(MissionEvidencePolicyModel).where(
                    MissionEvidencePolicyModel.goal_id == goal_id
                )
            )
            return self._policy_to_dict(row, mission_id=mission_id, goal_id=goal_id)

    async def upsert_evidence_policy(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionEvidencePolicyUpsert,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            self._require_goal(session, mission_id, goal_id)
            row = session.scalar(
                select(MissionEvidencePolicyModel).where(
                    MissionEvidencePolicyModel.goal_id == goal_id
                )
            )
            if row is None:
                row = MissionEvidencePolicyModel(
                    mission_id=mission_id,
                    goal_id=goal_id,
                )
                session.add(row)
            row.enabled = request.enabled
            row.required_evidence_count = request.required_evidence_count
            row.min_individual_score = request.min_individual_score
            row.min_average_score = request.min_average_score
            row.require_distinct_sources = request.require_distinct_sources
            row.min_distinct_sources = request.min_distinct_sources
            row.require_human_review = request.require_human_review
            row.auto_confirm_enabled = request.auto_confirm_enabled
            row.allowed_evidence_types_json = [value.value for value in request.allowed_evidence_types]
            row.metadata_json = dict(request.metadata)
            session.flush()
            result = self._policy_to_dict(row, mission_id=mission_id, goal_id=goal_id)
        await self._publish(
            "mission.evidence.policy.updated",
            workspace_id=mission.workspace_id,
            mission_id=mission_id,
            payload={"goal_id": goal_id, "policy": result},
        )
        return result

    async def submit_evidence(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionEvidenceSubmit,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            self._require_goal(session, mission_id, goal_id)
            if request.cycle_id is not None:
                self._require_cycle(session, mission_id, request.cycle_id)
            policy = self._policy_row(session, goal_id)
            allowed_types = (
                list(policy.allowed_evidence_types_json or [])
                if policy is not None
                else [value.value for value in MissionEvidenceType]
            )
            if request.evidence_type.value not in allowed_types:
                raise AutonomousMissionError(
                    f"Evidence type {request.evidence_type.value} запрещён политикой Goal."
                )
            digest = content_hash(
                evidence_type=request.evidence_type.value,
                source_ref=request.source_ref,
                content=request.content,
            )
            existing = session.scalar(
                select(MissionEvidenceModel).where(
                    MissionEvidenceModel.goal_id == goal_id,
                    MissionEvidenceModel.content_hash == digest,
                )
            )
            if existing is not None:
                result = self._evidence_to_dict(existing)
                result["duplicate"] = True
                return result
            row = MissionEvidenceModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=goal_id,
                cycle_id=request.cycle_id,
                evidence_type=request.evidence_type.value,
                source_type=request.source_type,
                source_ref=request.source_ref,
                submitted_by=request.submitted_by,
                content_json=dict(request.content),
                content_hash=digest,
                status=MissionEvidenceStatus.SUBMITTED.value,
                metadata_json=dict(request.metadata),
            )
            session.add(row)
            session.flush()
            evidence_id = row.id
            result = self._evidence_to_dict(row)

        await self._publish(
            "mission.evidence.submitted",
            workspace_id=result["workspace_id"],
            mission_id=mission_id,
            payload={"goal_id": goal_id, "evidence": result},
        )
        if request.auto_evaluate:
            evaluated = await self.evaluate_evidence(
                evidence_id,
                MissionEvidenceEvaluateRequest(auto_confirm=request.auto_confirm),
            )
            return evaluated
        return result

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(MissionEvidenceModel, evidence_id)
            return None if row is None else self._evidence_to_dict(row)

    def list_evidence(
        self,
        mission_id: str,
        *,
        goal_id: str | None = None,
        status: str | None = None,
        evidence_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionEvidenceModel).where(
                MissionEvidenceModel.mission_id == mission_id
            )
            if goal_id is not None:
                statement = statement.where(MissionEvidenceModel.goal_id == goal_id)
            if status is not None:
                statement = statement.where(MissionEvidenceModel.status == status)
            if evidence_type is not None:
                statement = statement.where(MissionEvidenceModel.evidence_type == evidence_type)
            rows = list(
                session.scalars(
                    statement.order_by(MissionEvidenceModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._evidence_to_dict(row) for row in rows]

    async def evaluate_evidence(
        self,
        evidence_id: str,
        request: MissionEvidenceEvaluateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            evidence = session.get(MissionEvidenceModel, evidence_id)
            if evidence is None:
                raise AutonomousMissionError("Evidence не найдено.")
            goal = self._require_goal(session, evidence.mission_id, evidence.goal_id)
            mission = self._require_mission(session, evidence.mission_id)
            policy = self._policy_row(session, evidence.goal_id)
            policy_dict = self._policy_to_dict(
                policy,
                mission_id=evidence.mission_id,
                goal_id=evidence.goal_id,
            )
            scores = self._score_evidence(evidence, goal, mission)
            evidence.relevance_score = scores["relevance_score"]
            evidence.quality_score = scores["quality_score"]
            evidence.verifiability_score = scores["verifiability_score"]
            evidence.aggregate_score = scores["aggregate_score"]
            evidence.evaluation_json = {
                **scores,
                "evaluator_ref": request.evaluator_ref,
                "policy_snapshot": policy_dict,
            }
            evidence.evaluated_at = utc_now()
            threshold = float(policy_dict["min_individual_score"])
            if policy_dict["require_human_review"]:
                evidence.status = MissionEvidenceStatus.NEEDS_REVIEW.value
            elif evidence.aggregate_score >= threshold:
                evidence.status = MissionEvidenceStatus.ACCEPTED.value
                evidence.rejection_reason = None
            elif evidence.aggregate_score >= max(0.0, threshold - 15.0):
                evidence.status = MissionEvidenceStatus.NEEDS_REVIEW.value
            else:
                evidence.status = MissionEvidenceStatus.REJECTED.value
                evidence.rejection_reason = "Aggregate evidence score is below policy threshold."
            self._evaluations += 1
            result = self._evidence_to_dict(evidence)

        await self._publish(
            "mission.evidence.evaluated",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={"goal_id": result["goal_id"], "evidence": result},
        )
        goal_evaluation = None
        if request.auto_confirm and result["status"] == MissionEvidenceStatus.ACCEPTED.value:
            goal_evaluation = await self.evaluate_goal(
                result["mission_id"],
                result["goal_id"],
                MissionGoalEvidenceEvaluateRequest(auto_confirm=True),
            )
        return {"evidence": result, "goal_evaluation": goal_evaluation}

    async def review_evidence(
        self,
        evidence_id: str,
        request: MissionEvidenceReviewRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            evidence = session.get(MissionEvidenceModel, evidence_id)
            if evidence is None:
                raise AutonomousMissionError("Evidence не найдено.")
            evidence.reviewed_by = request.actor_id
            evidence.reviewed_at = utc_now()
            if request.decision == "accept":
                evidence.status = MissionEvidenceStatus.ACCEPTED.value
                evidence.rejection_reason = None
            else:
                evidence.status = MissionEvidenceStatus.REJECTED.value
                evidence.rejection_reason = request.reason
            result = self._evidence_to_dict(evidence)

        await self._publish(
            "mission.evidence.reviewed",
            workspace_id=result["workspace_id"],
            mission_id=result["mission_id"],
            payload={
                "goal_id": result["goal_id"],
                "evidence": result,
                "decision": request.decision,
                "actor_id": request.actor_id,
                "reason": request.reason,
            },
        )
        goal_evaluation = None
        if request.auto_confirm and request.decision == "accept":
            goal_evaluation = await self.evaluate_goal(
                result["mission_id"],
                result["goal_id"],
                MissionGoalEvidenceEvaluateRequest(auto_confirm=True),
            )
        return {"evidence": result, "goal_evaluation": goal_evaluation}

    async def evaluate_goal(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionGoalEvidenceEvaluateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            goal = self._require_goal(session, mission_id, goal_id)
            policy = self._policy_row(session, goal_id)
            policy_dict = self._policy_to_dict(
                policy,
                mission_id=mission_id,
                goal_id=goal_id,
            )
            accepted = list(
                session.scalars(
                    select(MissionEvidenceModel)
                    .where(
                        MissionEvidenceModel.goal_id == goal_id,
                        MissionEvidenceModel.status == MissionEvidenceStatus.ACCEPTED.value,
                    )
                    .order_by(MissionEvidenceModel.created_at.asc())
                ).all()
            )
            scores = [float(row.aggregate_score) for row in accepted]
            average = round(sum(scores) / len(scores), 2) if scores else 0.0
            source_keys = {
                row.source_ref or f"{row.source_type}:{row.submitted_by}"
                for row in accepted
            }
            reasons: list[str] = []
            if mission.status != MissionStatus.ACTIVE.value:
                reasons.append("Mission is not active.")
            if not policy_dict["enabled"]:
                reasons.append("Evidence policy disabled.")
            if len(accepted) < policy_dict["required_evidence_count"]:
                reasons.append("Not enough accepted evidence.")
            if any(score < policy_dict["min_individual_score"] for score in scores):
                reasons.append("At least one evidence score is below the individual threshold.")
            if average < policy_dict["min_average_score"]:
                reasons.append("Average evidence score is below the policy threshold.")
            if (
                policy_dict["require_distinct_sources"]
                and len(source_keys) < policy_dict["min_distinct_sources"]
            ):
                reasons.append("Not enough distinct evidence sources.")
            if policy_dict["require_human_review"]:
                reasons.append("Human review is required.")
            eligible = not reasons
            evidence_ids = [row.id for row in accepted]
            already_achieved = goal.status == MissionGoalStatus.ACHIEVED.value
            workspace_id = mission.workspace_id

        confirmed = None
        if (
            request.auto_confirm
            and eligible
            and policy_dict["auto_confirm_enabled"]
            and not already_achieved
        ):
            confirmed = await self._confirm_goal(
                mission_id=mission_id,
                goal_id=goal_id,
                actor_id="builtin.evidence-evaluator",
                reason="Goal automatically confirmed by evidence policy.",
                evidence_ids=evidence_ids,
                aggregate_score=average,
                automatic=True,
                metadata={"policy": policy_dict},
            )
            self._automatic_confirmations += 1
        result = {
            "mission_id": mission_id,
            "goal_id": goal_id,
            "workspace_id": workspace_id,
            "eligible": eligible,
            "already_achieved": already_achieved,
            "accepted_evidence_count": len(accepted),
            "distinct_source_count": len(source_keys),
            "average_score": average,
            "evidence_ids": evidence_ids,
            "reasons": reasons,
            "policy": policy_dict,
            "automatic_confirmation": confirmed,
        }
        await self._publish(
            "mission.goal.evidence.evaluated",
            workspace_id=workspace_id,
            mission_id=mission_id,
            payload=result,
        )
        return result

    async def confirm_goal(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionGoalConfirmRequest,
    ) -> dict[str, Any]:
        scores: list[float] = []
        with self._session_factory() as session:
            for evidence_id in request.evidence_ids:
                evidence = session.get(MissionEvidenceModel, evidence_id)
                if evidence is None or evidence.goal_id != goal_id:
                    raise AutonomousMissionError(
                        f"Evidence {evidence_id} не относится к указанной Goal."
                    )
                scores.append(float(evidence.aggregate_score))
        aggregate = round(sum(scores) / len(scores), 2) if scores else 100.0
        return await self._confirm_goal(
            mission_id=mission_id,
            goal_id=goal_id,
            actor_id=request.actor_id,
            reason=request.reason,
            evidence_ids=request.evidence_ids,
            aggregate_score=aggregate,
            automatic=False,
            metadata=request.metadata,
        )

    async def reopen_goal(
        self,
        mission_id: str,
        goal_id: str,
        request: MissionGoalReopenRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            goal = self._require_goal(session, mission_id, goal_id)
            if goal.status != MissionGoalStatus.ACHIEVED.value:
                raise MissionStateError("Reopen разрешён только для achieved Goal.")
            previous_status = goal.status
            dependencies = list(goal.depends_on_json or [])
            dependency_rows = list(
                session.scalars(
                    select(MissionGoalModel).where(
                        MissionGoalModel.mission_id == mission_id,
                        MissionGoalModel.goal_key.in_(dependencies),
                    )
                ).all()
            ) if dependencies else []
            unblocked = all(
                row.status == MissionGoalStatus.ACHIEVED.value
                for row in dependency_rows
            )
            goal.status = (
                MissionGoalStatus.ACTIVE.value
                if unblocked
                else MissionGoalStatus.PENDING.value
            )
            goal.progress_percent = request.progress_percent
            goal.completed_at = None
            goal.result_json = {
                **(goal.result_json or {}),
                "reopened": {
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                    "at": iso(utc_now()),
                },
            }
            if mission.status == MissionStatus.COMPLETED.value:
                mission.status = MissionStatus.ACTIVE.value
                mission.completed_at = None
            confirmation = MissionGoalConfirmationModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=goal_id,
                decision=MissionGoalConfirmationDecision.REOPENED.value,
                automatic=False,
                actor_id=request.actor_id,
                reason=request.reason,
                evidence_ids_json=[],
                aggregate_score=0.0,
                previous_status=previous_status,
                new_status=goal.status,
                metadata_json=dict(request.metadata),
            )
            session.add(confirmation)
            self._recalculate_mission_progress(session, mission)
            mission.version += 1
            mission.last_activity_at = utc_now()
            session.flush()
            result = {
                "mission": self._mission_summary(mission),
                "goal": self._goal_to_dict(goal),
                "confirmation": self._confirmation_to_dict(confirmation),
            }

        await self._publish(
            "mission.goal.reopened",
            workspace_id=result["mission"]["workspace_id"],
            mission_id=mission_id,
            payload=result,
        )
        return result

    def list_confirmations(
        self,
        mission_id: str,
        *,
        goal_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            self._require_mission(session, mission_id)
            statement = select(MissionGoalConfirmationModel).where(
                MissionGoalConfirmationModel.mission_id == mission_id
            )
            if goal_id is not None:
                statement = statement.where(MissionGoalConfirmationModel.goal_id == goal_id)
            rows = list(
                session.scalars(
                    statement.order_by(MissionGoalConfirmationModel.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [self._confirmation_to_dict(row) for row in rows]

    async def handle_execution_event(self, event: Event) -> None:
        if event.event_type != "execution_plan.runtime.completed":
            return
        plan_id = str(event.payload.get("plan_id") or event.correlation_id or "")
        if not plan_id:
            return
        evidence_ids: list[str] = []
        with self._session_factory() as session:
            cycle = session.scalar(
                select(MissionCycleModel).where(
                    MissionCycleModel.execution_plan_id == plan_id
                )
            )
            if cycle is None:
                return
            mission = self._require_mission(session, cycle.mission_id)
            goal_rows = self._cycle_goals(session, cycle)
            event_payload = event.to_dict()
            for goal in goal_rows:
                content = {
                    "plan_id": plan_id,
                    "cycle_id": cycle.id,
                    "cycle_number": cycle.cycle_number,
                    "goal_key": goal.goal_key,
                    "status": "completed",
                    "event": event_payload,
                }
                digest = content_hash(
                    evidence_type=MissionEvidenceType.EXECUTION_RESULT.value,
                    source_ref=plan_id,
                    content=content,
                )
                existing = session.scalar(
                    select(MissionEvidenceModel).where(
                        MissionEvidenceModel.goal_id == goal.id,
                        MissionEvidenceModel.content_hash == digest,
                    )
                )
                if existing is not None:
                    continue
                row = MissionEvidenceModel(
                    workspace_id=mission.workspace_id,
                    mission_id=mission.id,
                    goal_id=goal.id,
                    cycle_id=cycle.id,
                    evidence_type=MissionEvidenceType.EXECUTION_RESULT.value,
                    source_type="execution_plan",
                    source_ref=plan_id,
                    submitted_by="system",
                    content_json=content,
                    content_hash=digest,
                    status=MissionEvidenceStatus.SUBMITTED.value,
                    metadata_json={"event_id": event.id},
                )
                session.add(row)
                session.flush()
                evidence_ids.append(row.id)
            self._upsert_memory_row(
                session,
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                goal_id=None,
                memory_key=f"cycle.{cycle.cycle_number}.execution_result",
                category="summary",
                content={
                    "plan_id": plan_id,
                    "cycle_id": cycle.id,
                    "goal_ids": [row.id for row in goal_rows],
                    "event": event_payload,
                },
                source_type="execution_plan",
                source_ref=plan_id,
                importance_score=80,
                confidence_score=90,
                created_by="system",
            )
        self._execution_evidence_captured += len(evidence_ids)
        for evidence_id in evidence_ids:
            await self.evaluate_evidence(
                evidence_id,
                MissionEvidenceEvaluateRequest(auto_confirm=True),
            )

    async def _confirm_goal(
        self,
        *,
        mission_id: str,
        goal_id: str,
        actor_id: str,
        reason: str,
        evidence_ids: list[str],
        aggregate_score: float,
        automatic: bool,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            mission = self._require_mission(session, mission_id)
            goal = self._require_goal(session, mission_id, goal_id)
            if mission.status in {MissionStatus.FAILED.value, MissionStatus.CANCELLED.value}:
                raise MissionStateError("Нельзя подтвердить Goal завершённой с ошибкой Mission.")
            previous_status = goal.status
            previous_progress = float(goal.progress_percent)
            if previous_status == MissionGoalStatus.ACHIEVED.value:
                latest = session.scalar(
                    select(MissionGoalConfirmationModel)
                    .where(
                        MissionGoalConfirmationModel.goal_id == goal_id,
                        MissionGoalConfirmationModel.decision == MissionGoalConfirmationDecision.CONFIRMED.value,
                    )
                    .order_by(MissionGoalConfirmationModel.created_at.desc())
                )
                return {
                    "mission": self._mission_summary(mission),
                    "goal": self._goal_to_dict(goal),
                    "confirmation": None if latest is None else self._confirmation_to_dict(latest),
                    "idempotent": True,
                }
            for evidence_id in evidence_ids:
                evidence = session.get(MissionEvidenceModel, evidence_id)
                if evidence is None or evidence.goal_id != goal_id:
                    raise AutonomousMissionError(
                        f"Evidence {evidence_id} не относится к указанной Goal."
                    )
                if automatic and evidence.status != MissionEvidenceStatus.ACCEPTED.value:
                    raise AutonomousMissionError(
                        "Автоматическое подтверждение допускает только accepted evidence."
                    )
            now = utc_now()
            goal.status = MissionGoalStatus.ACHIEVED.value
            goal.progress_percent = 100.0
            goal.completed_at = now
            goal.started_at = goal.started_at or now
            goal.result_json = {
                **(goal.result_json or {}),
                "confirmation": {
                    "automatic": automatic,
                    "actor_id": actor_id,
                    "reason": reason,
                    "evidence_ids": list(evidence_ids),
                    "aggregate_score": aggregate_score,
                    "confirmed_at": iso(now),
                },
            }
            confirmation = MissionGoalConfirmationModel(
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=goal_id,
                decision=MissionGoalConfirmationDecision.CONFIRMED.value,
                automatic=automatic,
                actor_id=actor_id,
                reason=reason,
                evidence_ids_json=list(evidence_ids),
                aggregate_score=max(0.0, min(100.0, aggregate_score)),
                previous_status=previous_status,
                new_status=goal.status,
                metadata_json=dict(metadata),
            )
            session.add(confirmation)
            session.add(
                MissionProgressUpdateModel(
                    mission_id=mission_id,
                    goal_id=goal_id,
                    actor_id=actor_id,
                    previous_progress_percent=previous_progress,
                    new_progress_percent=100.0,
                    message=reason,
                    evidence_json={"evidence_ids": list(evidence_ids)},
                    metadata_json={"automatic": automatic, **dict(metadata)},
                )
            )
            self._upsert_memory_row(
                session,
                workspace_id=mission.workspace_id,
                mission_id=mission_id,
                goal_id=goal_id,
                memory_key=f"goal.{goal.goal_key}.confirmation",
                category="decision",
                content={
                    "goal_id": goal_id,
                    "goal_key": goal.goal_key,
                    "decision": "confirmed",
                    "automatic": automatic,
                    "actor_id": actor_id,
                    "reason": reason,
                    "evidence_ids": list(evidence_ids),
                    "aggregate_score": aggregate_score,
                    "confirmed_at": iso(now),
                },
                source_type="evidence_evaluator" if automatic else "human_decision",
                source_ref=None,
                importance_score=100,
                confidence_score=int(round(aggregate_score)),
                created_by=actor_id,
            )
            self._activate_unblocked_goals(session, mission_id)
            self._recalculate_mission_progress(session, mission)
            mission.version += 1
            mission.last_activity_at = now
            if self._all_goals_achieved(session, mission_id):
                mission.status = MissionStatus.COMPLETED.value
                mission.progress_percent = 100.0
                mission.completed_at = now
                mission.next_cycle_at = None
            session.flush()
            result = {
                "mission": self._mission_summary(mission),
                "goal": self._goal_to_dict(goal),
                "confirmation": self._confirmation_to_dict(confirmation),
                "idempotent": False,
            }

        await self._publish(
            "mission.goal.confirmed",
            workspace_id=result["mission"]["workspace_id"],
            mission_id=mission_id,
            payload=result,
        )
        if result["mission"]["status"] == MissionStatus.COMPLETED.value:
            await self._publish(
                "mission.completed",
                workspace_id=result["mission"]["workspace_id"],
                mission_id=mission_id,
                payload={
                    "reason": "All Mission goals confirmed by evidence.",
                    "confirmation_id": result["confirmation"]["id"],
                },
            )
        return result

    @staticmethod
    def _score_evidence(
        evidence: MissionEvidenceModel,
        goal: MissionGoalModel,
        mission: WorkspaceMissionModel,
    ) -> dict[str, float | list[str]]:
        content = dict(evidence.content_json or {})
        keys = {str(key).casefold() for key in content}
        content_text = canonical_json(content).casefold()

        quality = 20.0
        if content:
            quality += 25.0
        quality += min(30.0, len(content) * 6.0)
        if keys & {"result", "status", "passed", "metrics", "summary", "value"}:
            quality += 25.0
        quality = min(100.0, quality)

        verifiability = 10.0
        if evidence.source_ref:
            verifiability += 25.0
        if evidence.source_type not in {"manual", "unknown"}:
            verifiability += 15.0
        trace_keys = {
            "url", "artifact_id", "checksum", "hash", "run_id", "plan_id",
            "test_id", "document_id", "receipt_id",
        }
        if keys & trace_keys:
            verifiability += 30.0
        if keys & {"passed", "status", "metrics", "result"}:
            verifiability += 20.0
        verifiability = min(100.0, verifiability)

        goal_text = " ".join(
            [goal.title, goal.description, goal.success_criteria, mission.objective]
        ).casefold()
        goal_tokens = {
            token.casefold()
            for token in _WORD_RE.findall(goal_text)
            if token.casefold() not in _STOP_WORDS
        }
        evidence_tokens = {
            token.casefold()
            for token in _WORD_RE.findall(content_text)
            if token.casefold() not in _STOP_WORDS
        }
        overlap = len(goal_tokens & evidence_tokens)
        denominator = max(1, min(len(goal_tokens), 10))
        relevance = min(100.0, 45.0 + (overlap / denominator) * 55.0)

        aggregate = round(
            quality * 0.35 + verifiability * 0.35 + relevance * 0.30,
            2,
        )
        return {
            "quality_score": round(quality, 2),
            "verifiability_score": round(verifiability, 2),
            "relevance_score": round(relevance, 2),
            "aggregate_score": aggregate,
            "matched_goal_tokens": sorted(goal_tokens & evidence_tokens)[:25],
        }

    def _policy_row(
        self,
        session: Session,
        goal_id: str,
    ) -> MissionEvidencePolicyModel | None:
        return session.scalar(
            select(MissionEvidencePolicyModel).where(
                MissionEvidencePolicyModel.goal_id == goal_id
            )
        )

    @staticmethod
    def _policy_to_dict(
        row: MissionEvidencePolicyModel | None,
        *,
        mission_id: str,
        goal_id: str,
    ) -> dict[str, Any]:
        if row is None:
            return {
                "id": None,
                "mission_id": mission_id,
                "goal_id": goal_id,
                "enabled": True,
                "required_evidence_count": 1,
                "min_individual_score": 70.0,
                "min_average_score": 75.0,
                "require_distinct_sources": False,
                "min_distinct_sources": 1,
                "require_human_review": True,
                "auto_confirm_enabled": False,
                "allowed_evidence_types": [value.value for value in MissionEvidenceType],
                "metadata": {},
                "created_at": None,
                "updated_at": None,
                "persisted": False,
            }
        return {
            "id": row.id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "enabled": row.enabled,
            "required_evidence_count": row.required_evidence_count,
            "min_individual_score": float(row.min_individual_score),
            "min_average_score": float(row.min_average_score),
            "require_distinct_sources": row.require_distinct_sources,
            "min_distinct_sources": row.min_distinct_sources,
            "require_human_review": row.require_human_review,
            "auto_confirm_enabled": row.auto_confirm_enabled,
            "allowed_evidence_types": list(row.allowed_evidence_types_json or []),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "persisted": True,
        }

    def _require_mission(self, session: Session, mission_id: str) -> WorkspaceMissionModel:
        mission = session.get(WorkspaceMissionModel, mission_id)
        if mission is None:
            raise MissionNotFound(f"Mission {mission_id} не найдена.")
        return mission

    def _require_goal(
        self,
        session: Session,
        mission_id: str,
        goal_id: str,
    ) -> MissionGoalModel:
        goal = session.get(MissionGoalModel, goal_id)
        if goal is None or goal.mission_id != mission_id:
            raise MissionGoalNotFound(f"Goal {goal_id} не найдена.")
        return goal

    def _require_cycle(
        self,
        session: Session,
        mission_id: str,
        cycle_id: str,
    ) -> MissionCycleModel:
        cycle = session.get(MissionCycleModel, cycle_id)
        if cycle is None or cycle.mission_id != mission_id:
            raise MissionCycleNotFound(f"Mission Cycle {cycle_id} не найден.")
        return cycle

    def _cycle_goals(
        self,
        session: Session,
        cycle: MissionCycleModel,
    ) -> list[MissionGoalModel]:
        goal_ids = list(cycle.goal_ids_json or [])
        statement = select(MissionGoalModel).where(
            MissionGoalModel.mission_id == cycle.mission_id
        )
        if goal_ids:
            statement = statement.where(MissionGoalModel.id.in_(goal_ids))
        else:
            statement = statement.where(
                MissionGoalModel.status.in_([
                    MissionGoalStatus.ACTIVE.value,
                    MissionGoalStatus.PENDING.value,
                ])
            )
        return list(session.scalars(statement).all())

    @staticmethod
    def _upsert_memory_row(
        session: Session,
        *,
        workspace_id: str,
        mission_id: str,
        goal_id: str | None,
        memory_key: str,
        category: str,
        content: dict[str, Any],
        source_type: str,
        source_ref: str | None,
        importance_score: int,
        confidence_score: int,
        created_by: str,
    ) -> MissionMemoryEntryModel:
        row = session.scalar(
            select(MissionMemoryEntryModel).where(
                MissionMemoryEntryModel.mission_id == mission_id,
                MissionMemoryEntryModel.memory_key == memory_key,
            )
        )
        if row is None:
            row = MissionMemoryEntryModel(
                workspace_id=workspace_id,
                mission_id=mission_id,
                memory_key=memory_key,
            )
            session.add(row)
        else:
            row.version += 1
        row.goal_id = goal_id
        row.category = category
        row.content_json = dict(content)
        row.source_type = source_type
        row.source_ref = source_ref
        row.importance_score = max(0, min(100, importance_score))
        row.confidence_score = max(0, min(100, confidence_score))
        row.created_by = created_by
        row.archived = False
        return row

    @staticmethod
    def _activate_unblocked_goals(session: Session, mission_id: str) -> None:
        rows = list(
            session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission_id
                )
            ).all()
        )
        by_key = {row.goal_key: row for row in rows}
        now = utc_now()
        for row in rows:
            if row.status != MissionGoalStatus.PENDING.value:
                continue
            dependencies = [by_key.get(key) for key in (row.depends_on_json or [])]
            if all(
                dependency is not None
                and dependency.status == MissionGoalStatus.ACHIEVED.value
                for dependency in dependencies
            ):
                row.status = MissionGoalStatus.ACTIVE.value
                row.started_at = row.started_at or now

    @staticmethod
    def _recalculate_mission_progress(
        session: Session,
        mission: WorkspaceMissionModel,
    ) -> None:
        goals = list(
            session.scalars(
                select(MissionGoalModel).where(
                    MissionGoalModel.mission_id == mission.id
                )
            ).all()
        )
        total_weight = sum(float(goal.weight) for goal in goals)
        if total_weight <= 0:
            return
        mission.progress_percent = round(
            sum(float(goal.weight) * float(goal.progress_percent) for goal in goals)
            / total_weight,
            2,
        )

    @staticmethod
    def _all_goals_achieved(session: Session, mission_id: str) -> bool:
        total = int(
            session.scalar(
                select(func.count())
                .select_from(MissionGoalModel)
                .where(MissionGoalModel.mission_id == mission_id)
            )
            or 0
        )
        not_achieved = int(
            session.scalar(
                select(func.count())
                .select_from(MissionGoalModel)
                .where(
                    MissionGoalModel.mission_id == mission_id,
                    MissionGoalModel.status != MissionGoalStatus.ACHIEVED.value,
                )
            )
            or 0
        )
        return total > 0 and not_achieved == 0

    @staticmethod
    def _memory_to_dict(row: MissionMemoryEntryModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "memory_key": row.memory_key,
            "category": row.category,
            "content": dict(row.content_json or {}),
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "importance_score": row.importance_score,
            "confidence_score": row.confidence_score,
            "version": row.version,
            "archived": row.archived,
            "expires_at": iso(row.expires_at),
            "created_by": row.created_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _evidence_to_dict(row: MissionEvidenceModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "cycle_id": row.cycle_id,
            "evidence_type": row.evidence_type,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "submitted_by": row.submitted_by,
            "content": dict(row.content_json or {}),
            "content_hash": row.content_hash,
            "status": row.status,
            "relevance_score": float(row.relevance_score),
            "quality_score": float(row.quality_score),
            "verifiability_score": float(row.verifiability_score),
            "aggregate_score": float(row.aggregate_score),
            "evaluation": dict(row.evaluation_json or {}),
            "metadata": dict(row.metadata_json or {}),
            "evaluated_at": iso(row.evaluated_at),
            "reviewed_by": row.reviewed_by,
            "reviewed_at": iso(row.reviewed_at),
            "rejection_reason": row.rejection_reason,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _confirmation_to_dict(row: MissionGoalConfirmationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "mission_id": row.mission_id,
            "goal_id": row.goal_id,
            "decision": row.decision,
            "automatic": row.automatic,
            "actor_id": row.actor_id,
            "reason": row.reason,
            "evidence_ids": list(row.evidence_ids_json or []),
            "aggregate_score": float(row.aggregate_score),
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _mission_summary(row: WorkspaceMissionModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "status": row.status,
            "progress_percent": float(row.progress_percent),
            "version": row.version,
            "completed_at": iso(row.completed_at),
            "last_activity_at": iso(row.last_activity_at),
        }

    @staticmethod
    def _goal_to_dict(row: MissionGoalModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "mission_id": row.mission_id,
            "goal_key": row.goal_key,
            "title": row.title,
            "status": row.status,
            "progress_percent": float(row.progress_percent),
            "result": dict(row.result_json or {}),
            "started_at": iso(row.started_at),
            "completed_at": iso(row.completed_at),
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
                source="mission_memory_service",
                workspace_id=workspace_id,
                correlation_id=mission_id,
                payload={"mission_id": mission_id, **payload},
            )
        )
