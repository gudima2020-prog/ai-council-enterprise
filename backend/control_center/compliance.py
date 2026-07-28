from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import event as sqlalchemy_event, func, select
from sqlalchemy.orm import Session

from backend.control_center.compliance_schemas import (
    HumanControlAccessFindingDecision,
    HumanControlAccessFindingDecisionRequest,
    HumanControlAccessReviewCompleteRequest,
    HumanControlAccessReviewCreate,
    HumanControlAuditExportRequest,
    HumanControlComplianceReportCreate,
)
from backend.control_center.models import (
    HumanControlAccessReviewFindingModel,
    HumanControlAccessReviewModel,
    HumanControlApiTokenModel,
    HumanControlApprovalCaseModel,
    HumanControlBreakGlassModel,
    HumanControlComplianceReportModel,
    HumanControlIdentityModel,
    HumanControlNotificationModel,
    HumanControlOperatorAuditEventModel,
    HumanControlRoleBindingModel,
    HumanControlRoleModel,
    HumanControlSecurityEventModel,
    HumanControlSessionModel,
)
from backend.control_center.service import (
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
    ensure_utc,
    iso,
    utc_now,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
GENESIS_HASH = "0" * 64
PRIVILEGED_ROLE_KEYS = {"owner", "control_admin", "risk_officer", "auditor"}
PRIVILEGED_PERMISSIONS = {
    "*",
    "human_control.override",
    "human_control.manage_roles",
    "human_control.manage_policies",
    "human_control.auth.manage",
    "human_control.auth.token_manage",
    "human_control.auth.break_glass",
    "human_control.compliance.manage",
    "human_control.access_review",
}
SENSITIVE_KEY_FRAGMENTS = {
    "password",
    "access_token",
    "activation_token",
    "csrf_token",
    "session_token",
    "api_token",
    "secret",
    "authorization",
    "cookie",
}


class HumanControlComplianceImmutabilityError(RuntimeError):
    pass


@sqlalchemy_event.listens_for(HumanControlOperatorAuditEventModel, "before_update")
def _block_operator_audit_update(mapper, connection, target) -> None:
    raise HumanControlComplianceImmutabilityError(
        "Operator audit records are immutable and cannot be updated."
    )


@sqlalchemy_event.listens_for(HumanControlOperatorAuditEventModel, "before_delete")
def _block_operator_audit_delete(mapper, connection, target) -> None:
    raise HumanControlComplianceImmutabilityError(
        "Operator audit records are immutable and cannot be deleted."
    )


@sqlalchemy_event.listens_for(HumanControlComplianceReportModel, "before_update")
def _block_compliance_report_update(mapper, connection, target) -> None:
    raise HumanControlComplianceImmutabilityError(
        "Compliance reports are immutable and cannot be updated."
    )


@sqlalchemy_event.listens_for(HumanControlComplianceReportModel, "before_delete")
def _block_compliance_report_delete(mapper, connection, target) -> None:
    raise HumanControlComplianceImmutabilityError(
        "Compliance reports are immutable and cannot be deleted."
    )


def normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return normalize_json(value.value)
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): normalize_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_json(item) for item in value]
    if hasattr(value, "model_dump"):
        return normalize_json(value.model_dump())
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def redact_json(value: Any) -> Any:
    normalized = normalize_json(value)
    if isinstance(normalized, dict):
        result: dict[str, Any] = {}
        for key, item in normalized.items():
            lowered = key.lower()
            protected_reference = lowered.endswith(
                ("_id", "_prefix", "_ref", "_key")
            )
            if not protected_reference and any(
                fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_json(item)
        return result
    if isinstance(normalized, list):
        return [redact_json(item) for item in normalized]
    return normalized


def coerce_datetime(value: Any, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = default or utc_now()
    else:
        parsed = default or utc_now()
    return ensure_utc(parsed) or utc_now()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


class HumanControlComplianceService:
    """Tamper-evident operator journal, reports and privileged-access review."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._write_lock = asyncio.Lock()
        self._runtime_recorded = 0
        self._runtime_duplicates = 0

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            audit_count = session.scalar(
                select(func.count()).select_from(HumanControlOperatorAuditEventModel)
            ) or 0
            report_count = session.scalar(
                select(func.count()).select_from(HumanControlComplianceReportModel)
            ) or 0
            open_reviews = session.scalar(
                select(func.count())
                .select_from(HumanControlAccessReviewModel)
                .where(HumanControlAccessReviewModel.status == "open")
            ) or 0
            open_findings = session.scalar(
                select(func.count())
                .select_from(HumanControlAccessReviewFindingModel)
                .where(HumanControlAccessReviewFindingModel.status == "open")
            ) or 0
            head = session.scalar(
                select(HumanControlOperatorAuditEventModel)
                .order_by(HumanControlOperatorAuditEventModel.sequence.desc())
                .limit(1)
            )
        return {
            "service": "human_control_compliance",
            "audit_entries": int(audit_count),
            "reports": int(report_count),
            "open_access_reviews": int(open_reviews),
            "open_access_findings": int(open_findings),
            "head_sequence": 0 if head is None else head.sequence,
            "head_hash": GENESIS_HASH if head is None else head.event_hash,
            "runtime_recorded": self._runtime_recorded,
            "runtime_duplicates": self._runtime_duplicates,
            "hash_algorithm": "SHA-256",
            "delivery_semantics": "append-only hash chain",
        }

    async def record_event(self, event: Event) -> dict[str, Any]:
        payload = redact_json(event.payload or {})
        event_id = str(event.id)
        event_type = str(event.event_type)
        occurred_at = coerce_datetime(event.created_at)
        workspace_id = self._string_or_none(
            event.workspace_id or self._find_value(payload, ("workspace_id",))
        )
        actor_id = self._string_or_none(
            self._find_value(
                payload,
                (
                    "actor_id",
                    "created_by",
                    "updated_by",
                    "requested_by",
                    "resolved_by",
                    "approved_by",
                    "initiated_by",
                    "granted_by",
                ),
            )
        )
        identity_id = self._string_or_none(
            self._find_value(payload, ("identity_id", "requested_by_identity_id"))
        )
        auth_method = self._string_or_none(
            self._find_value(payload, ("auth_method",))
        )
        resource_type, resource_id = self._extract_resource(event_type, payload)
        outcome = self._outcome(event_type, payload)
        risk_level = self._risk(payload)
        action = event_type.rsplit(".", 1)[-1][:96]

        async with self._write_lock:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(HumanControlOperatorAuditEventModel).where(
                        HumanControlOperatorAuditEventModel.event_id == event_id
                    )
                )
                if existing is not None:
                    self._runtime_duplicates += 1
                    return self._audit_to_dict(existing)

                previous = session.scalar(
                    select(HumanControlOperatorAuditEventModel)
                    .order_by(HumanControlOperatorAuditEventModel.sequence.desc())
                    .limit(1)
                )
                sequence = 1 if previous is None else previous.sequence + 1
                previous_hash = GENESIS_HASH if previous is None else previous.event_hash
                recorded_at = utc_now()
                material = self._audit_hash_material(
                    sequence=sequence,
                    event_id=event_id,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    source=str(event.source),
                    actor_id=actor_id,
                    identity_id=identity_id,
                    auth_method=auth_method,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome=outcome,
                    risk_level=risk_level,
                    correlation_id=event.correlation_id,
                    payload=payload,
                    occurred_at=occurred_at,
                    recorded_at=recorded_at,
                    previous_hash=previous_hash,
                )
                row = HumanControlOperatorAuditEventModel(
                    sequence=sequence,
                    event_id=event_id,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    source=str(event.source),
                    actor_id=actor_id,
                    identity_id=identity_id,
                    auth_method=auth_method,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome=outcome,
                    risk_level=risk_level,
                    correlation_id=event.correlation_id,
                    payload_json=payload,
                    occurred_at=occurred_at,
                    recorded_at=recorded_at,
                    previous_hash=previous_hash,
                    event_hash=sha256_json(material),
                )
                session.add(row)
                session.flush()
                result = self._audit_to_dict(row)
            self._runtime_recorded += 1
            return result

    def list_audit_events(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        outcome: str | None = None,
        risk_level: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlOperatorAuditEventModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.workspace_id == workspace_id
                )
            if actor_id is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.actor_id == actor_id
                )
            if event_type is not None:
                if "*" in event_type:
                    statement = statement.where(
                        HumanControlOperatorAuditEventModel.event_type.like(
                            event_type.replace("*", "%")
                        )
                    )
                else:
                    statement = statement.where(
                        HumanControlOperatorAuditEventModel.event_type == event_type
                    )
            if outcome is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.outcome == outcome
                )
            if risk_level is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.risk_level == risk_level
                )
            if period_start is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.occurred_at
                    >= (ensure_utc(period_start) or period_start)
                )
            if period_end is not None:
                statement = statement.where(
                    HumanControlOperatorAuditEventModel.occurred_at
                    < (ensure_utc(period_end) or period_end)
                )
            rows = session.scalars(
                statement.order_by(
                    HumanControlOperatorAuditEventModel.sequence.desc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._audit_to_dict(row) for row in rows]

    def get_audit_event(self, audit_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlOperatorAuditEventModel, audit_id)
            return None if row is None else self._audit_to_dict(row)

    def verify_chain(self) -> dict[str, Any]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(HumanControlOperatorAuditEventModel).order_by(
                    HumanControlOperatorAuditEventModel.sequence.asc()
                )
            ).all()

        expected_sequence = 1
        expected_previous = GENESIS_HASH
        failures: list[dict[str, Any]] = []
        for row in rows:
            if row.sequence != expected_sequence:
                failures.append(
                    {
                        "sequence": row.sequence,
                        "error": "sequence_gap",
                        "expected": expected_sequence,
                    }
                )
            if row.previous_hash != expected_previous:
                failures.append(
                    {
                        "sequence": row.sequence,
                        "error": "previous_hash_mismatch",
                    }
                )
            material = self._audit_hash_material(
                sequence=row.sequence,
                event_id=row.event_id,
                workspace_id=row.workspace_id,
                event_type=row.event_type,
                source=row.source,
                actor_id=row.actor_id,
                identity_id=row.identity_id,
                auth_method=row.auth_method,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                outcome=row.outcome,
                risk_level=row.risk_level,
                correlation_id=row.correlation_id,
                payload=row.payload_json,
                occurred_at=row.occurred_at,
                recorded_at=row.recorded_at,
                previous_hash=row.previous_hash,
            )
            calculated = sha256_json(material)
            if calculated != row.event_hash:
                failures.append(
                    {
                        "sequence": row.sequence,
                        "error": "event_hash_mismatch",
                        "stored": row.event_hash,
                        "calculated": calculated,
                    }
                )
            expected_previous = row.event_hash
            expected_sequence = row.sequence + 1
        return {
            "valid": not failures,
            "entries": len(rows),
            "head_sequence": 0 if not rows else rows[-1].sequence,
            "head_hash": GENESIS_HASH if not rows else rows[-1].event_hash,
            "failures": failures[:100],
        }

    def export_audit(self, request: HumanControlAuditExportRequest) -> dict[str, Any]:
        rows = self.list_audit_events(
            workspace_id=request.workspace_id,
            actor_id=request.actor_id,
            event_type=request.event_type,
            period_start=request.period_start,
            period_end=request.period_end,
            limit=request.limit,
        )
        if not request.include_payloads:
            for row in rows:
                row.pop("payload", None)
        package = {
            "schema": "ai-studio-human-control-audit-export-v1",
            "generated_at": iso(utc_now()),
            "filters": normalize_json(request.model_dump()),
            "chain_verification": self.verify_chain(),
            "entries": rows,
        }
        package["evidence_hash"] = sha256_json(package)
        return package

    async def generate_report(
        self,
        request: HumanControlComplianceReportCreate,
    ) -> dict[str, Any]:
        period_end = ensure_utc(request.period_end) or utc_now()
        period_start = ensure_utc(request.period_start) or period_end - timedelta(days=30)
        report_type = _enum_value(request.report_type)
        with self._session_factory() as session:
            audit_rows = session.scalars(
                select(HumanControlOperatorAuditEventModel)
                .where(
                    HumanControlOperatorAuditEventModel.occurred_at >= period_start,
                    HumanControlOperatorAuditEventModel.occurred_at < period_end,
                )
                .order_by(HumanControlOperatorAuditEventModel.sequence.asc())
            ).all()
            if request.workspace_id is not None:
                audit_rows = [
                    row for row in audit_rows if row.workspace_id == request.workspace_id
                ]

            activity = self._activity_report(audit_rows, request.include_event_payloads)
            privileged = self._privileged_inventory(
                session,
                workspace_id=request.workspace_id,
                include_expired=False,
                include_sessions=True,
            )
            authentication = self._authentication_report(
                session,
                workspace_id=request.workspace_id,
                period_start=period_start,
                period_end=period_end,
            )
            governance = self._governance_report(
                session,
                workspace_id=request.workspace_id,
                period_start=period_start,
                period_end=period_end,
            )
            sections: dict[str, Any] = {}
            if report_type in {"activity_summary", "full"}:
                sections["activity"] = activity
            if report_type in {"privileged_access", "full"}:
                sections["privileged_access"] = privileged["summary"]
            if report_type in {"authentication", "full"}:
                sections["authentication"] = authentication
            if report_type in {"approval_governance", "full"}:
                sections["approval_governance"] = governance

            report_payload = {
                "schema": "ai-studio-human-control-compliance-v1",
                "report_type": report_type,
                "workspace_id": request.workspace_id,
                "period_start": iso(period_start),
                "period_end": iso(period_end),
                "generated_by": request.generated_by,
                "chain_verification": self.verify_chain(),
                "sections": sections,
                "metadata": redact_json(request.metadata),
            }
            summary = {
                "audit_entries": len(audit_rows),
                "actors": len({row.actor_id for row in audit_rows if row.actor_id}),
                "failures": sum(1 for row in audit_rows if row.outcome == "failure"),
                "high_risk_events": sum(
                    1 for row in audit_rows if row.risk_level in {"high", "critical"}
                ),
                "privileged_findings": privileged["summary"]["finding_count"],
            }
            evidence_hash = sha256_json(report_payload)
            row = HumanControlComplianceReportModel(
                workspace_id=request.workspace_id,
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
                generated_by=request.generated_by,
                summary_json=summary,
                report_json=report_payload,
                evidence_hash=evidence_hash,
                metadata_json=redact_json(request.metadata),
            )
            session.add(row)
            session.flush()
            result = self._report_to_dict(row)

        await self._publish(
            "human_control.compliance.report.generated",
            request.workspace_id,
            {
                "report": {
                    "id": result["id"],
                    "report_type": report_type,
                    "evidence_hash": evidence_hash,
                },
                "actor_id": request.generated_by,
            },
        )
        return result

    def list_reports(
        self,
        *,
        workspace_id: str | None = None,
        report_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlComplianceReportModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlComplianceReportModel.workspace_id == workspace_id
                )
            if report_type is not None:
                statement = statement.where(
                    HumanControlComplianceReportModel.report_type == report_type
                )
            rows = session.scalars(
                statement.order_by(
                    HumanControlComplianceReportModel.generated_at.desc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._report_to_dict(row) for row in rows]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlComplianceReportModel, report_id)
            return None if row is None else self._report_to_dict(row)

    async def start_access_review(
        self,
        request: HumanControlAccessReviewCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlAccessReviewModel).where(
                    HumanControlAccessReviewModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if existing is not None:
                return self._review_to_dict(existing)

            inventory = self._privileged_inventory(
                session,
                workspace_id=request.workspace_id,
                include_expired=request.include_expired,
                include_sessions=request.include_sessions,
            )
            evidence = {
                "workspace_id": request.workspace_id,
                "created_at": iso(utc_now()),
                "findings": inventory["findings"],
            }
            review = HumanControlAccessReviewModel(
                workspace_id=request.workspace_id,
                idempotency_key=request.idempotency_key,
                title=request.title,
                status="open",
                initiated_by=request.initiated_by,
                include_expired=request.include_expired,
                include_sessions=request.include_sessions,
                summary_json=inventory["summary"],
                evidence_hash=sha256_json(evidence),
                metadata_json=redact_json(request.metadata),
            )
            session.add(review)
            session.flush()
            for definition in inventory["findings"]:
                session.add(
                    HumanControlAccessReviewFindingModel(
                        review_id=review.id,
                        workspace_id=request.workspace_id,
                        fingerprint=definition["fingerprint"],
                        actor_id=definition.get("actor_id"),
                        identity_id=definition.get("identity_id"),
                        finding_type=definition["finding_type"],
                        severity=definition["severity"],
                        status="open",
                        resource_type=definition["resource_type"],
                        resource_id=definition["resource_id"],
                        title=definition["title"],
                        details_json=definition["details"],
                        recommended_action=definition["recommended_action"],
                        metadata_json={},
                    )
                )
            session.flush()
            result = self._review_to_dict(review)

        await self._publish(
            "human_control.access_review.started",
            request.workspace_id,
            {
                "review": result,
                "actor_id": request.initiated_by,
                "risk_level": "high" if result["summary"].get("high_or_critical", 0) else "medium",
            },
        )
        return result

    def list_access_reviews(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlAccessReviewModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlAccessReviewModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlAccessReviewModel.status == status
                )
            rows = session.scalars(
                statement.order_by(HumanControlAccessReviewModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._review_to_dict(row) for row in rows]

    def get_access_review(self, review_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlAccessReviewModel, review_id)
            return None if row is None else self._review_to_dict(row)

    def list_findings(
        self,
        *,
        review_id: str | None = None,
        workspace_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        actor_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlAccessReviewFindingModel)
            if review_id is not None:
                statement = statement.where(
                    HumanControlAccessReviewFindingModel.review_id == review_id
                )
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlAccessReviewFindingModel.workspace_id == workspace_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlAccessReviewFindingModel.status == status
                )
            if severity is not None:
                statement = statement.where(
                    HumanControlAccessReviewFindingModel.severity == severity
                )
            if actor_id is not None:
                statement = statement.where(
                    HumanControlAccessReviewFindingModel.actor_id == actor_id
                )
            rows = session.scalars(
                statement.order_by(
                    HumanControlAccessReviewFindingModel.created_at.asc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._finding_to_dict(row) for row in rows]

    async def decide_finding(
        self,
        finding_id: str,
        request: HumanControlAccessFindingDecisionRequest,
    ) -> dict[str, Any]:
        decision = _enum_value(request.decision)
        with self._session_factory() as session:
            finding = session.get(HumanControlAccessReviewFindingModel, finding_id)
            if finding is None:
                raise HumanControlNotFound("Результат проверки доступа не найден.")
            if finding.status != "open":
                if request.force:
                    pass
                else:
                    raise HumanControlConflict("Результат проверки уже обработан.")
            review = session.get(HumanControlAccessReviewModel, finding.review_id)
            if review is None or review.status != "open":
                raise HumanControlConflict("Проверка доступа уже закрыта.")

            if decision == HumanControlAccessFindingDecision.REMEDIATE.value:
                if not request.apply_change:
                    raise HumanControlConflict(
                        "Для remediation требуется apply_change=true."
                    )
                self._apply_remediation(session, finding, request.actor_id, request.reason)
                finding.status = "remediated"
            elif decision == HumanControlAccessFindingDecision.ACCEPT.value:
                finding.status = "accepted"
            elif decision == HumanControlAccessFindingDecision.DISMISS.value:
                finding.status = "dismissed"
            else:
                raise HumanControlError("Неизвестное решение по результату проверки.")

            finding.resolution = request.reason
            finding.resolved_by = request.actor_id
            finding.resolved_at = utc_now()
            finding.metadata_json = redact_json(request.metadata)
            session.flush()
            result = self._finding_to_dict(finding)

        await self._publish(
            "human_control.access_review.finding.resolved",
            result["workspace_id"],
            {
                "finding": result,
                "actor_id": request.actor_id,
                "decision": decision,
                "risk_level": result["severity"],
            },
        )
        return result

    async def complete_access_review(
        self,
        review_id: str,
        request: HumanControlAccessReviewCompleteRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            review = session.get(HumanControlAccessReviewModel, review_id)
            if review is None:
                raise HumanControlNotFound("Проверка привилегированного доступа не найдена.")
            if review.status != "open":
                return self._review_to_dict(review)
            findings = session.scalars(
                select(HumanControlAccessReviewFindingModel).where(
                    HumanControlAccessReviewFindingModel.review_id == review.id
                )
            ).all()
            open_count = sum(1 for row in findings if row.status == "open")
            if open_count and not request.force:
                raise HumanControlConflict(
                    f"Остаются необработанные результаты проверки: {open_count}."
                )
            statuses = Counter(row.status for row in findings)
            severities = Counter(row.severity for row in findings)
            review.status = "completed"
            review.completed_by = request.actor_id
            review.completed_at = utc_now()
            review.completion_reason = request.reason
            review.summary_json = {
                **dict(review.summary_json or {}),
                "status_counts": dict(statuses),
                "severity_counts": dict(severities),
                "open_findings": open_count,
            }
            review.evidence_hash = sha256_json(
                {
                    "review_id": review.id,
                    "summary": review.summary_json,
                    "findings": [self._finding_to_dict(row) for row in findings],
                    "completed_by": request.actor_id,
                    "completed_at": iso(review.completed_at),
                }
            )
            session.flush()
            result = self._review_to_dict(review)

        await self._publish(
            "human_control.access_review.completed",
            result["workspace_id"],
            {
                "review": result,
                "actor_id": request.actor_id,
                "risk_level": "medium",
            },
        )
        return result

    def dashboard(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            audit_statement = select(HumanControlOperatorAuditEventModel)
            finding_statement = select(HumanControlAccessReviewFindingModel)
            review_statement = select(HumanControlAccessReviewModel)
            if workspace_id is not None:
                audit_statement = audit_statement.where(
                    HumanControlOperatorAuditEventModel.workspace_id == workspace_id
                )
                finding_statement = finding_statement.where(
                    HumanControlAccessReviewFindingModel.workspace_id == workspace_id
                )
                review_statement = review_statement.where(
                    HumanControlAccessReviewModel.workspace_id == workspace_id
                )
            audit_rows = session.scalars(audit_statement).all()
            findings = session.scalars(finding_statement).all()
            reviews = session.scalars(review_statement).all()
        return {
            "workspace_id": workspace_id,
            "audit_entries": len(audit_rows),
            "audit_by_outcome": dict(Counter(row.outcome for row in audit_rows)),
            "audit_by_risk": dict(Counter(row.risk_level for row in audit_rows)),
            "access_reviews": dict(Counter(row.status for row in reviews)),
            "access_findings": dict(Counter(row.status for row in findings)),
            "finding_severity": dict(Counter(row.severity for row in findings)),
            "chain": self.verify_chain(),
        }

    def _privileged_inventory(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        include_expired: bool,
        include_sessions: bool,
    ) -> dict[str, Any]:
        now = utc_now()
        identities = {
            row.id: row
            for row in session.scalars(select(HumanControlIdentityModel)).all()
        }
        identities_by_actor = {row.actor_id: row for row in identities.values()}
        roles = {row.id: row for row in session.scalars(select(HumanControlRoleModel)).all()}
        findings: list[dict[str, Any]] = []

        binding_statement = select(HumanControlRoleBindingModel)
        if workspace_id is not None:
            binding_statement = binding_statement.where(
                HumanControlRoleBindingModel.workspace_id == workspace_id
            )
        for binding in session.scalars(binding_statement).all():
            if not binding.enabled:
                continue
            expires_at = ensure_utc(binding.expires_at)
            if not include_expired and expires_at is not None and expires_at <= now:
                continue
            role = roles.get(binding.role_id)
            if role is None or not role.enabled:
                continue
            permissions = set(role.permissions_json or [])
            if role.role_key not in PRIVILEGED_ROLE_KEYS and not (
                permissions & PRIVILEGED_PERMISSIONS
            ):
                continue
            identity = identities_by_actor.get(binding.actor_id)
            severity = "critical" if role.role_key in {"owner", "control_admin"} else "high"
            finding_type = "privileged_role_binding"
            if identity is not None and identity.status != "active":
                severity = "critical"
                finding_type = "disabled_identity_privileged_binding"
            details = {
                "role_key": role.role_key,
                "permissions": sorted(permissions),
                "binding_expires_at": iso(binding.expires_at),
                "granted_by": binding.granted_by,
                "identity_status": None if identity is None else identity.status,
            }
            findings.append(
                self._finding_definition(
                    actor_id=binding.actor_id,
                    identity_id=None if identity is None else identity.id,
                    finding_type=finding_type,
                    severity=severity,
                    resource_type="role_binding",
                    resource_id=binding.id,
                    title=f"Привилегированная роль: {role.role_key}",
                    details=details,
                    recommended_action=(
                        "Подтвердить служебную необходимость, ограничить срок "
                        "или отозвать назначение роли."
                    ),
                )
            )

        token_statement = select(HumanControlApiTokenModel)
        if workspace_id is not None:
            token_statement = token_statement.where(
                HumanControlApiTokenModel.workspace_id == workspace_id
            )
        for token in session.scalars(token_statement).all():
            expires_at = ensure_utc(token.expires_at)
            active = token.status == "active" and (
                expires_at is None or expires_at > now or include_expired
            )
            if not active:
                continue
            identity = identities.get(token.identity_id)
            actor_id = None if identity is None else identity.actor_id
            scopes = set(token.scopes_json or [])
            privileged = not scopes or bool(scopes & PRIVILEGED_PERMISSIONS) or "*" in scopes
            if not privileged:
                continue
            severity = "high" if expires_at is None else "medium"
            finding_type = "unbounded_api_token" if expires_at is None else "privileged_api_token"
            if identity is not None and identity.status != "active":
                severity = "critical"
                finding_type = "disabled_identity_active_token"
            findings.append(
                self._finding_definition(
                    actor_id=actor_id,
                    identity_id=token.identity_id,
                    finding_type=finding_type,
                    severity=severity,
                    resource_type="api_token",
                    resource_id=token.id,
                    title=f"Привилегированный API-токен: {token.name}",
                    details={
                        "scopes": sorted(scopes),
                        "expires_at": iso(token.expires_at),
                        "last_used_at": iso(token.last_used_at),
                        "identity_status": None if identity is None else identity.status,
                        "token_prefix": token.token_prefix,
                    },
                    recommended_action=(
                        "Проверить scopes и срок действия; отозвать неиспользуемый "
                        "или бессрочный токен."
                    ),
                )
            )

        break_statement = select(HumanControlBreakGlassModel).where(
            HumanControlBreakGlassModel.status.in_(("approved", "active"))
        )
        if workspace_id is not None:
            break_statement = break_statement.where(
                HumanControlBreakGlassModel.workspace_id == workspace_id
            )
        for access in session.scalars(break_statement).all():
            identity = identities.get(access.requested_by_identity_id)
            findings.append(
                self._finding_definition(
                    actor_id=None if identity is None else identity.actor_id,
                    identity_id=access.requested_by_identity_id,
                    finding_type="active_break_glass",
                    severity="critical",
                    resource_type="break_glass",
                    resource_id=access.id,
                    title="Активный аварийный доступ break-glass",
                    details={
                        "status": access.status,
                        "scopes": list(access.scopes_json or []),
                        "reason": access.reason,
                        "expires_at": iso(access.expires_at),
                    },
                    recommended_action=(
                        "Проверить инцидент, завершить аварийную сессию и отозвать "
                        "доступ после восстановления штатного управления."
                    ),
                )
            )

        if include_sessions:
            session_statement = select(HumanControlSessionModel).where(
                HumanControlSessionModel.status == "active"
            )
            if workspace_id is not None:
                session_statement = session_statement.where(
                    HumanControlSessionModel.workspace_id == workspace_id
                )
            for auth_session in session.scalars(session_statement).all():
                if ensure_utc(auth_session.expires_at) <= now and not include_expired:
                    continue
                identity = identities.get(auth_session.identity_id)
                if auth_session.auth_method != "break_glass" and not (
                    identity is not None and identity.status != "active"
                ):
                    continue
                severity = "critical" if auth_session.auth_method == "break_glass" else "high"
                findings.append(
                    self._finding_definition(
                        actor_id=None if identity is None else identity.actor_id,
                        identity_id=auth_session.identity_id,
                        finding_type=(
                            "break_glass_session"
                            if auth_session.auth_method == "break_glass"
                            else "disabled_identity_active_session"
                        ),
                        severity=severity,
                        resource_type="session",
                        resource_id=auth_session.id,
                        title="Привилегированная активная сессия",
                        details={
                            "auth_method": auth_session.auth_method,
                            "scopes": list(auth_session.scopes_json or []),
                            "expires_at": iso(auth_session.expires_at),
                            "last_seen_at": iso(auth_session.last_seen_at),
                            "client_ip": auth_session.client_ip,
                        },
                        recommended_action=(
                            "Проверить активность сессии и отозвать её при отсутствии "
                            "подтверждённой необходимости."
                        ),
                    )
                )

        severity_counts = Counter(item["severity"] for item in findings)
        actor_count = len({item.get("actor_id") for item in findings if item.get("actor_id")})
        return {
            "summary": {
                "finding_count": len(findings),
                "actor_count": actor_count,
                "severity_counts": dict(severity_counts),
                "high_or_critical": severity_counts.get("high", 0)
                + severity_counts.get("critical", 0),
            },
            "findings": findings,
        }

    def _authentication_report(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, Any]:
        statement = select(HumanControlSecurityEventModel).where(
            HumanControlSecurityEventModel.created_at >= period_start,
            HumanControlSecurityEventModel.created_at < period_end,
        )
        if workspace_id is not None:
            statement = statement.where(
                HumanControlSecurityEventModel.workspace_id == workspace_id
            )
        rows = session.scalars(statement).all()
        return {
            "events": len(rows),
            "successful": sum(1 for row in rows if row.success),
            "failed": sum(1 for row in rows if not row.success),
            "by_type": dict(Counter(row.event_type for row in rows)),
            "actors": len({row.actor_id for row in rows if row.actor_id}),
        }

    def _governance_report(
        self,
        session: Session,
        *,
        workspace_id: str | None,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, Any]:
        case_statement = select(HumanControlApprovalCaseModel).where(
            HumanControlApprovalCaseModel.created_at >= period_start,
            HumanControlApprovalCaseModel.created_at < period_end,
        )
        notification_statement = select(HumanControlNotificationModel).where(
            HumanControlNotificationModel.created_at >= period_start,
            HumanControlNotificationModel.created_at < period_end,
        )
        if workspace_id is not None:
            case_statement = case_statement.where(
                HumanControlApprovalCaseModel.workspace_id == workspace_id
            )
            notification_statement = notification_statement.where(
                HumanControlNotificationModel.workspace_id == workspace_id
            )
        cases = session.scalars(case_statement).all()
        notifications = session.scalars(notification_statement).all()
        return {
            "approval_cases": len(cases),
            "approval_case_statuses": dict(Counter(row.status for row in cases)),
            "notifications": len(notifications),
            "notification_statuses": dict(
                Counter(row.status for row in notifications)
            ),
            "acknowledgement_statuses": dict(
                Counter(row.ack_status for row in notifications)
            ),
        }

    @staticmethod
    def _activity_report(
        rows: list[HumanControlOperatorAuditEventModel],
        include_payloads: bool,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "events": len(rows),
            "by_type": dict(Counter(row.event_type for row in rows)),
            "by_actor": dict(Counter(row.actor_id or "system" for row in rows)),
            "by_outcome": dict(Counter(row.outcome for row in rows)),
            "by_risk": dict(Counter(row.risk_level for row in rows)),
        }
        if include_payloads:
            result["entries"] = [
                HumanControlComplianceService._audit_to_dict(row) for row in rows
            ]
        return result

    def _apply_remediation(
        self,
        session: Session,
        finding: HumanControlAccessReviewFindingModel,
        actor_id: str,
        reason: str,
    ) -> None:
        now = utc_now()
        if finding.resource_type == "role_binding":
            row = session.get(HumanControlRoleBindingModel, finding.resource_id)
            if row is None:
                raise HumanControlNotFound("Назначение роли уже отсутствует.")
            row.enabled = False
            row.reason = f"{row.reason}\nAccess review remediation by {actor_id}: {reason}".strip()
        elif finding.resource_type == "api_token":
            row = session.get(HumanControlApiTokenModel, finding.resource_id)
            if row is None:
                raise HumanControlNotFound("API-токен уже отсутствует.")
            row.status = "revoked"
            row.revoked_at = now
            row.revoked_by = actor_id
            row.revoke_reason = reason
        elif finding.resource_type == "session":
            row = session.get(HumanControlSessionModel, finding.resource_id)
            if row is None:
                raise HumanControlNotFound("Сессия уже отсутствует.")
            row.status = "revoked"
            row.revoked_at = now
            row.revoked_by = actor_id
            row.revoke_reason = reason
        elif finding.resource_type == "break_glass":
            row = session.get(HumanControlBreakGlassModel, finding.resource_id)
            if row is None:
                raise HumanControlNotFound("Запрос break-glass уже отсутствует.")
            row.status = "revoked"
            row.resolved_at = now
            row.resolution_reason = reason
        else:
            raise HumanControlConflict(
                f"Автоматическая remediation не поддерживается: {finding.resource_type}."
            )

    @staticmethod
    def _finding_definition(
        *,
        actor_id: str | None,
        identity_id: str | None,
        finding_type: str,
        severity: str,
        resource_type: str,
        resource_id: str,
        title: str,
        details: dict[str, Any],
        recommended_action: str,
    ) -> dict[str, Any]:
        fingerprint = sha256_json(
            {
                "finding_type": finding_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        )
        return {
            "fingerprint": fingerprint,
            "actor_id": actor_id,
            "identity_id": identity_id,
            "finding_type": finding_type,
            "severity": severity,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "title": title,
            "details": redact_json(details),
            "recommended_action": recommended_action,
        }

    @staticmethod
    def _audit_hash_material(**values: Any) -> dict[str, Any]:
        values = dict(values)
        values["occurred_at"] = iso(values["occurred_at"])
        values["recorded_at"] = iso(values["recorded_at"])
        return normalize_json(values)

    @staticmethod
    def _audit_to_dict(row: HumanControlOperatorAuditEventModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "sequence": row.sequence,
            "event_id": row.event_id,
            "workspace_id": row.workspace_id,
            "event_type": row.event_type,
            "source": row.source,
            "actor_id": row.actor_id,
            "identity_id": row.identity_id,
            "auth_method": row.auth_method,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "outcome": row.outcome,
            "risk_level": row.risk_level,
            "correlation_id": row.correlation_id,
            "payload": dict(row.payload_json or {}),
            "occurred_at": iso(row.occurred_at),
            "recorded_at": iso(row.recorded_at),
            "previous_hash": row.previous_hash,
            "event_hash": row.event_hash,
        }

    @staticmethod
    def _report_to_dict(row: HumanControlComplianceReportModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "report_type": row.report_type,
            "period_start": iso(row.period_start),
            "period_end": iso(row.period_end),
            "generated_by": row.generated_by,
            "summary": dict(row.summary_json or {}),
            "report": dict(row.report_json or {}),
            "evidence_hash": row.evidence_hash,
            "metadata": dict(row.metadata_json or {}),
            "generated_at": iso(row.generated_at),
        }

    @staticmethod
    def _review_to_dict(row: HumanControlAccessReviewModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "idempotency_key": row.idempotency_key,
            "title": row.title,
            "status": row.status,
            "initiated_by": row.initiated_by,
            "include_expired": row.include_expired,
            "include_sessions": row.include_sessions,
            "summary": dict(row.summary_json or {}),
            "evidence_hash": row.evidence_hash,
            "metadata": dict(row.metadata_json or {}),
            "completed_by": row.completed_by,
            "completed_at": iso(row.completed_at),
            "completion_reason": row.completion_reason,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _finding_to_dict(row: HumanControlAccessReviewFindingModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "review_id": row.review_id,
            "workspace_id": row.workspace_id,
            "fingerprint": row.fingerprint,
            "actor_id": row.actor_id,
            "identity_id": row.identity_id,
            "finding_type": row.finding_type,
            "severity": row.severity,
            "status": row.status,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "title": row.title,
            "details": dict(row.details_json or {}),
            "recommended_action": row.recommended_action,
            "resolution": row.resolution,
            "resolved_by": row.resolved_by,
            "resolved_at": iso(row.resolved_at),
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _find_value(payload: Any, keys: tuple[str, ...]) -> Any:
        if not isinstance(payload, dict):
            return None
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        for value in payload.values():
            if isinstance(value, dict):
                found = HumanControlComplianceService._find_value(value, keys)
                if found not in (None, ""):
                    return found
        return None

    @staticmethod
    def _extract_resource(
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        candidates = (
            ("item_id", "item"),
            ("case_id", "approval_case"),
            ("notification_id", "notification"),
            ("escalation_id", "escalation"),
            ("session_id", "session"),
            ("token_id", "api_token"),
            ("identity_id", "identity"),
            ("review_id", "access_review"),
            ("finding_id", "access_finding"),
            ("report_id", "compliance_report"),
            ("source_id", "source"),
            ("id", event_type.split(".")[1] if "." in event_type else "human_control"),
        )
        for key, resource_type in candidates:
            value = HumanControlComplianceService._find_value(payload, (key,))
            if value not in (None, ""):
                return resource_type, str(value)
        return None, None

    @staticmethod
    def _outcome(event_type: str, payload: dict[str, Any]) -> str:
        lowered = event_type.lower()
        failure_terms = (
            "failed",
            "failure",
            "rejected",
            "denied",
            "revoked",
            "expired",
            "cancelled",
            "unroutable",
            "overdue",
            "invalid",
        )
        if any(term in lowered for term in failure_terms):
            return "failure"
        success = HumanControlComplianceService._find_value(payload, ("success",))
        if success is False:
            return "failure"
        success_terms = (
            "created",
            "updated",
            "approved",
            "acknowledged",
            "resolved",
            "completed",
            "delivered",
            "activated",
            "login.succeeded",
            "granted",
            "claimed",
        )
        return "success" if any(term in lowered for term in success_terms) else "unknown"

    @staticmethod
    def _risk(payload: dict[str, Any]) -> str:
        value = HumanControlComplianceService._find_value(
            payload,
            ("risk_level", "severity"),
        )
        normalized = str(value or "medium").lower()
        return normalized if normalized in {"low", "medium", "high", "critical"} else "medium"

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return None if value in (None, "") else str(value)

    async def _publish(
        self,
        event_type: str,
        workspace_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="human_control.compliance",
                workspace_id=workspace_id,
                payload=redact_json(payload),
            )
        )
