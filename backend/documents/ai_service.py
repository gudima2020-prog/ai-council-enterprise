from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.documents.ai_analysis import (
    DocumentAIAnalysisError,
    DocumentAIResponseContract,
    DocumentAIWorkflow,
    build_primary_prompts,
    build_reviewer_prompts,
)
from backend.documents.ai_context import (
    ConservativeTokenEstimator,
    DeterministicDocumentAIContextBuilder,
    DocumentAIContextResult,
    DocumentContextSelection,
    DocumentContextTokenBudget,
)
from backend.documents.ai_repository import DocumentAIAnalysisRepository
from backend.documents.ai_resolver import DocumentAIContextResolver
from backend.documents.models import (
    DocumentAIAnalysisCitationModel,
    DocumentAIAnalysisRunModel,
)
from backend.gateway.schemas import GatewayError, GatewayResponse
from backend.gateway.service import AIGateway
from backend.repositories.models import ModelRepository
from backend.runtime_policy import (
    PolicyAction,
    PolicyOperation,
    ProviderTrust,
    RuntimePolicyContext,
    RuntimePolicyDecision,
    RuntimePolicyEngine,
)
from backend.services.workspace_policy import WorkspacePolicyService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentAIApprovalCredentials:
    approval_id: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.approval_id, str)
            or not self.approval_id.strip()
            or self.approval_id != self.approval_id.strip()
            or len(self.approval_id) > 255
        ):
            raise ValueError("approval_id must be non-empty and trimmed")
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("approval token must be non-empty")
        if len(self.token) > 4096:
            raise ValueError("approval token exceeds the bounded limit")


@dataclass(frozen=True, slots=True)
class DocumentAIRequestSpec:
    workspace_id: str
    workflow: DocumentAIWorkflow
    selection: DocumentContextSelection
    provider: str
    model: str
    reviewer_provider: str
    reviewer_model: str
    question: str | None = None
    max_output_tokens: int = 1_000
    reviewer_max_output_tokens: int = 512
    retention_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, DocumentAIWorkflow):
            raise TypeError("workflow must be a DocumentAIWorkflow")
        if not isinstance(self.selection, DocumentContextSelection):
            raise TypeError("selection must be a DocumentContextSelection")
        if self.workspace_id != self.selection.workspace_id:
            raise ValueError("selection Workspace does not match request Workspace")
        for name in (
            "workspace_id",
            "provider",
            "model",
            "reviewer_provider",
            "reviewer_model",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 255
            ):
                raise ValueError(f"{name} must be non-empty and trimmed")
        if self.provider.casefold() == "auto" or (
            self.reviewer_provider.casefold() == "auto"
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_PROVIDER_EXPLICIT_REQUIRED",
                "Document AI requires explicit primary and reviewer providers.",
            )
        if (self.provider.casefold(), self.model) == (
            self.reviewer_provider.casefold(),
            self.reviewer_model,
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_REVIEWER_NOT_INDEPENDENT",
                "Primary and reviewer routes must use different models.",
            )
        for name, maximum in (
            ("max_output_tokens", 32_768),
            ("reviewer_max_output_tokens", 8_192),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > maximum
            ):
                raise ValueError(f"{name} is outside the bounded limit")
        if self.retention_days is not None and (
            isinstance(self.retention_days, bool)
            or not isinstance(self.retention_days, int)
            or self.retention_days < 1
            or self.retention_days > 3650
        ):
            raise ValueError("retention_days is outside the bounded limit")
        if self.question is not None and self.question != self.question.strip():
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_QUESTION_INVALID",
                "Question must be trimmed before document AI execution.",
            )
        build_primary_prompts(
            workflow=self.workflow,
            question=self.question,
            packed_context="preflight-context-placeholder",
        )


@dataclass(frozen=True, slots=True)
class DocumentAIModelSnapshot:
    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    supports_json: bool
    fingerprint: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "supports_json": self.supports_json,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class DocumentAIStagePolicy:
    stage: str
    provider: str
    provider_trust: ProviderTrust
    decision: RuntimePolicyDecision

    @property
    def external_warning_required(self) -> bool:
        return self.provider_trust not in {
            ProviderTrust.LOCAL,
            ProviderTrust.BLOCKED,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "provider": self.provider,
            "provider_trust": self.provider_trust.value,
            "runtime_policy": {
                "policy_version": self.decision.policy_version,
                "action": self.decision.action.value,
                "reason_codes": list(self.decision.reason_codes),
                "fingerprint": self.decision.fingerprint,
            },
            "external_provider_warning": (
                "Selected document content and generated answer may be sent "
                "to this external provider. Explicit acknowledgement is "
                "required before execution."
                if self.external_warning_required
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class DocumentAIPreflightResult:
    context: DocumentAIContextResult
    primary_model: DocumentAIModelSnapshot
    reviewer_model: DocumentAIModelSnapshot
    primary_policy: DocumentAIStagePolicy
    reviewer_policy: DocumentAIStagePolicy
    reviewer_planned_tokens: int
    temperature: float
    retention_policy: str
    retention_days: int

    @property
    def external_warning_required(self) -> bool:
        return any(
            item.external_warning_required
            for item in (self.primary_policy, self.reviewer_policy)
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_public_dict(include_context=False),
            "primary_model": self.primary_model.to_public_dict(),
            "reviewer_model": self.reviewer_model.to_public_dict(),
            "stages": [
                self.primary_policy.to_public_dict(),
                self.reviewer_policy.to_public_dict(),
            ],
            "reviewer_planned_tokens": self.reviewer_planned_tokens,
            "retention_policy": self.retention_policy,
            "retention_days": self.retention_days,
            "external_provider_warning_required": (
                self.external_warning_required
            ),
        }


@dataclass(frozen=True, slots=True)
class DocumentAIAnalysisRecord:
    row: DocumentAIAnalysisRunModel
    citations: tuple[dict[str, Any], ...]

    def to_public_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.row.id,
            "workspace_id": self.row.workspace_id,
            "workflow": self.row.workflow,
            "status": self.row.status,
            "idempotency_key": self.row.idempotency_key,
            "request_fingerprint": self.row.request_fingerprint,
            "selection_fingerprint": self.row.selection_fingerprint,
            "selected_sources": list(self.row.selected_sources_json or []),
            "context_manifest": dict(self.row.context_manifest_json or {}),
            "context_sha256": self.row.context_sha256,
            "effective_classification": self.row.effective_classification,
            "primary": {
                "provider": self.row.primary_provider,
                "model": self.row.primary_model,
                "request_id": self.row.primary_request_id,
            },
            "reviewer": {
                "provider": self.row.reviewer_provider,
                "model": self.row.reviewer_model,
                "request_id": self.row.reviewer_request_id,
                "verdict": dict(self.row.reviewer_verdict_json or {}),
                "response_sha256": self.row.reviewer_response_sha256,
            },
            "provider_trust": dict(self.row.provider_trust_json or {}),
            "external_provider_acknowledged": (
                self.row.external_provider_acknowledged
            ),
            "output_text_sha256": self.row.output_text_sha256,
            "output_citation_ids": list(
                self.row.output_citation_ids_json or []
            ),
            "citations": list(self.citations),
            "content_state": self.row.content_state,
            "content_purged_at": self.row.content_purged_at,
            "runtime_policy": dict(self.row.runtime_policy_json or {}),
            "approval_evidence": dict(
                self.row.approval_evidence_json or {}
            ),
            "provider_evidence": dict(
                self.row.provider_evidence_json or {}
            ),
            "error_code": self.row.error_code,
            "error_message": self.row.error_message,
            "error_details": dict(self.row.error_details_json or {}),
            "retention_policy": self.row.retention_policy,
            "retention_days": self.row.retention_days,
            "retention_expires_at": self.row.retention_expires_at,
            "requested_by": self.row.requested_by,
            "attempt_count": self.row.attempt_count,
            "started_at": self.row.started_at,
            "completed_at": self.row.completed_at,
            "failed_at": self.row.failed_at,
            "created_at": self.row.created_at,
            "updated_at": self.row.updated_at,
        }
        if include_content and self.row.content_state == "active":
            payload["question"] = self.row.request_text
            if self.row.status == "completed":
                payload["output_text"] = self.row.output_text
        return payload


@dataclass(frozen=True, slots=True)
class DocumentAIExecutionResult:
    record: DocumentAIAnalysisRecord
    created: bool
    reused: bool
    approval_required: bool
    approval_stage: str | None

    def to_public_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        return {
            "created": self.created,
            "reused": self.reused,
            "approval_required": self.approval_required,
            "approval_stage": self.approval_stage,
            "analysis": self.record.to_public_dict(
                include_content=include_content
            ),
        }


@dataclass(frozen=True, slots=True)
class DocumentAIRetentionPolicy:
    policy_version: str = "classification-retention-v1"
    public_days: int = 365
    internal_days: int = 180
    confidential_days: int = 90
    restricted_days: int = 30

    def resolve(self, *, classification: str, requested_days: int | None) -> int:
        try:
            maximum = {
                "public": self.public_days,
                "internal": self.internal_days,
                "confidential": self.confidential_days,
                "restricted": self.restricted_days,
            }[classification]
        except KeyError as exc:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_CLASSIFICATION_INVALID",
                "Document AI classification is unsupported.",
            ) from exc
        if requested_days is None:
            return maximum
        if requested_days > maximum:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_RETENTION_LIMIT",
                "retention_days exceeds the classification policy.",
                details={"maximum_retention_days": maximum},
            )
        return requested_days


class DocumentAIAnalysisService:
    SAFETY_MARGIN_TOKENS = 512
    REVIEWER_ANSWER_BYTES_PER_TOKEN = 4

    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        gateway: AIGateway,
        workspace_policy: WorkspacePolicyService,
        resolver: DocumentAIContextResolver | None = None,
        context_builder: DeterministicDocumentAIContextBuilder | None = None,
        retention_policy: DocumentAIRetentionPolicy | None = None,
        runtime_policy: RuntimePolicyEngine | None = None,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._gateway = gateway
        self._workspace_policy = workspace_policy
        self._resolver = resolver or DocumentAIContextResolver(session=session)
        self._context_builder = (
            context_builder or DeterministicDocumentAIContextBuilder()
        )
        self._retention = retention_policy or DocumentAIRetentionPolicy()
        self._runtime_policy = runtime_policy or RuntimePolicyEngine()
        self._models = ModelRepository(session)
        self._repository = DocumentAIAnalysisRepository(session)
        self._estimator = ConservativeTokenEstimator()

    def preflight(
        self,
        *,
        spec: DocumentAIRequestSpec,
        now: datetime | None = None,
    ) -> DocumentAIPreflightResult:
        if not isinstance(spec, DocumentAIRequestSpec):
            raise TypeError("spec must be a DocumentAIRequestSpec")
        timestamp = now or utc_now()
        sources = self._resolver.resolve(
            selection=spec.selection,
            now=timestamp,
        )
        primary_model = self._resolve_model(
            provider=spec.provider,
            model=spec.model,
            requested_output_tokens=spec.max_output_tokens,
        )
        reviewer_model = self._resolve_model(
            provider=spec.reviewer_provider,
            model=spec.reviewer_model,
            requested_output_tokens=spec.reviewer_max_output_tokens,
        )
        placeholder = "DOCUMENT_CONTEXT_PLACEHOLDER"
        primary_system, primary_user = build_primary_prompts(
            workflow=spec.workflow,
            question=spec.question,
            packed_context=placeholder,
        )
        framing_tokens = self._estimator.estimate(
            primary_user.replace(placeholder, "", 1)
        )
        context = self._context_builder.build(
            selection=spec.selection,
            sources=sources,
            token_budget=DocumentContextTokenBudget(
                context_window_tokens=primary_model.context_window,
                system_prompt_tokens=self._estimator.estimate(primary_system),
                user_prompt_tokens=framing_tokens,
                reserved_output_tokens=spec.max_output_tokens,
                safety_margin_tokens=self.SAFETY_MARGIN_TOKENS,
            ),
        )
        reviewer_planned_tokens = self._reviewer_preflight_tokens(
            packed_context=context.packed_context,
            primary_max_output_tokens=spec.max_output_tokens,
            reviewer_max_output_tokens=spec.reviewer_max_output_tokens,
        )
        if reviewer_planned_tokens > reviewer_model.context_window:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_REVIEW_CONTEXT_LIMIT",
                "Reviewer context preflight exceeds the model context window.",
                details={
                    "planned_tokens": reviewer_planned_tokens,
                    "context_window": reviewer_model.context_window,
                },
            )

        workspace_policy = self._workspace_policy.get_effective_policy(
            spec.workspace_id
        )
        primary_policy = self._stage_policy(
            stage="primary",
            provider=spec.provider,
            provider_trust=workspace_policy.provider_trust_for(spec.provider),
            classification=context.manifest.effective_classification,
            workspace_id=spec.workspace_id,
        )
        reviewer_policy = self._stage_policy(
            stage="reviewer",
            provider=spec.reviewer_provider,
            provider_trust=workspace_policy.provider_trust_for(
                spec.reviewer_provider
            ),
            classification=context.manifest.effective_classification,
            workspace_id=spec.workspace_id,
        )
        retention_days = self._retention.resolve(
            classification=(
                context.manifest.effective_classification.value
            ),
            requested_days=spec.retention_days,
        )
        return DocumentAIPreflightResult(
            context=context,
            primary_model=primary_model,
            reviewer_model=reviewer_model,
            primary_policy=primary_policy,
            reviewer_policy=reviewer_policy,
            reviewer_planned_tokens=reviewer_planned_tokens,
            temperature=float(workspace_policy.temperature),
            retention_policy=self._retention.policy_version,
            retention_days=retention_days,
        )

    async def execute(
        self,
        *,
        spec: DocumentAIRequestSpec,
        idempotency_key: str,
        external_provider_acknowledged: bool,
        actor_id: str | None = None,
        primary_approval: DocumentAIApprovalCredentials | None = None,
        reviewer_approval: DocumentAIApprovalCredentials | None = None,
        now: datetime | None = None,
    ) -> DocumentAIExecutionResult:
        timestamp = now or utc_now()
        self._validate_idempotency_key(idempotency_key)
        preflight = self.preflight(spec=spec, now=timestamp)
        self._assert_policy_admission(preflight)
        if (
            preflight.external_warning_required
            and external_provider_acknowledged is not True
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_EXTERNAL_ACK_REQUIRED",
                "Explicit external-provider acknowledgement is required.",
                details={
                    "external_stages": [
                        policy.stage
                        for policy in (
                            preflight.primary_policy,
                            preflight.reviewer_policy,
                        )
                        if policy.external_warning_required
                    ]
                },
            )
        retention_days = preflight.retention_days
        request_fingerprint = self._request_fingerprint(
            spec=spec,
            preflight=preflight,
            retention_days=retention_days,
        )
        row = self._repository.find_idempotency(
            workspace_id=spec.workspace_id,
            idempotency_key=idempotency_key,
        )
        created = row is None
        if row is None:
            row = self._repository.create_pending(
                workspace_id=spec.workspace_id,
                workflow=spec.workflow.value,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                selection_fingerprint=spec.selection.fingerprint,
                selected_sources=[
                    source.to_public_dict()
                    for source in spec.selection.sources
                ],
                context_manifest=(
                    preflight.context.manifest.to_public_dict()
                ),
                context_sha256=preflight.context.manifest.context_sha256,
                effective_classification=(
                    preflight.context.manifest.effective_classification.value
                ),
                primary_provider=spec.provider,
                primary_model=spec.model,
                primary_request_id=f"ai_req_{uuid.uuid4().hex}",
                reviewer_provider=spec.reviewer_provider,
                reviewer_model=spec.reviewer_model,
                reviewer_request_id=f"ai_req_{uuid.uuid4().hex}",
                provider_trust={
                    "primary": preflight.primary_policy.provider_trust.value,
                    "reviewer": preflight.reviewer_policy.provider_trust.value,
                },
                external_provider_acknowledged=(
                    external_provider_acknowledged
                ),
                request_text=spec.question,
                request_text_sha256=(
                    _text_sha256(spec.question.strip())
                    if spec.question is not None
                    else None
                ),
                retention_policy=self._retention.policy_version,
                retention_days=retention_days,
                retention_expires_at=(
                    timestamp + timedelta(days=retention_days)
                ),
                requested_by=actor_id,
                citations=preflight.context.manifest.citations,
                now=timestamp,
            )
            await self._publish(
                "document.ai.created",
                row=row,
                extra={
                    "selected_source_count": (
                        preflight.context.manifest.selected_source_count
                    ),
                    "fragment_count": (
                        preflight.context.manifest.fragment_count
                    ),
                },
            )
        else:
            self._assert_idempotent_match(
                row=row,
                request_fingerprint=request_fingerprint,
                preflight=preflight,
            )
            if row.status in {"primary_running", "reviewer_running"}:
                raise DocumentAIAnalysisError(
                    "DOCUMENT_AI_RUN_CONFLICT",
                    "The idempotent document AI run is already executing.",
                    details={"analysis_run_id": row.id, "status": row.status},
                )
            if row.content_state == "purged":
                return self._result(
                    row,
                    created=False,
                    reused=True,
                )
            if (
                row.status == "awaiting_primary_approval"
                and primary_approval is None
            ) or (
                row.status == "awaiting_reviewer_approval"
                and reviewer_approval is None
            ):
                return self._result(
                    row,
                    created=False,
                    reused=True,
                )
            if row.status in {"completed", "rejected", "failed"}:
                return self._result(
                    row,
                    created=False,
                    reused=True,
                )

        if row.status in {"pending", "awaiting_primary_approval"}:
            primary_result = await self._execute_primary(
                row=row,
                spec=spec,
                preflight=preflight,
                approval=primary_approval,
                actor_id=actor_id,
                now=timestamp,
            )
            if primary_result is not None:
                return DocumentAIExecutionResult(
                    record=self._record(row),
                    created=created,
                    reused=not created,
                    approval_required=True,
                    approval_stage="primary",
                )
            if row.status == "failed":
                return self._result(row, created=created, reused=False)

        if row.status in {
            "awaiting_reviewer",
            "awaiting_reviewer_approval",
        }:
            reviewer_result = await self._execute_reviewer(
                row=row,
                spec=spec,
                preflight=preflight,
                approval=reviewer_approval,
                actor_id=actor_id,
                now=timestamp,
            )
            if reviewer_result is not None:
                return DocumentAIExecutionResult(
                    record=self._record(row),
                    created=created,
                    reused=not created,
                    approval_required=True,
                    approval_stage="reviewer",
                )

        return self._result(row, created=created, reused=False)

    def get(
        self,
        *,
        analysis_run_id: str,
        workspace_id: str,
    ) -> DocumentAIAnalysisRecord:
        row = self._repository.get(
            analysis_run_id=analysis_run_id,
            workspace_id=workspace_id,
        )
        if row is None:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_RUN_NOT_FOUND",
                "Document AI run was not found in this Workspace.",
            )
        return self._record(row)

    def list(
        self,
        *,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentAIAnalysisRecord]:
        return [
            self._record(row)
            for row in self._repository.list_runs(
                workspace_id=workspace_id,
                limit=max(1, min(limit, 500)),
                offset=max(0, offset),
            )
        ]

    async def purge_expired(
        self,
        *,
        workspace_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = now or utc_now()
        result = self._repository.purge_expired(
            workspace_id=workspace_id,
            now=timestamp,
        )
        if result["runs"]:
            await self._event_bus.publish(
                Event(
                    event_type="document.ai.retention_purged",
                    source="documents.ai",
                    workspace_id=workspace_id,
                    payload={
                        "purged_runs": result["runs"],
                        "run_ids": list(result["run_ids"]),
                        "actor_id": actor_id,
                        "purged_at": timestamp.isoformat(),
                    },
                )
            )
        return {
            "workspace_id": workspace_id,
            "purged_runs": result["runs"],
            "run_ids": list(result["run_ids"]),
        }

    async def _execute_primary(
        self,
        *,
        row: DocumentAIAnalysisRunModel,
        spec: DocumentAIRequestSpec,
        preflight: DocumentAIPreflightResult,
        approval: DocumentAIApprovalCredentials | None,
        actor_id: str | None,
        now: datetime,
    ) -> str | None:
        self._repeat_model_preflight(preflight.primary_model)
        system_prompt, user_prompt = build_primary_prompts(
            workflow=spec.workflow,
            question=spec.question,
            packed_context=preflight.context.packed_context,
        )
        self._repository.start_stage(row, stage="primary", now=now)
        response = await self._gateway_call(
            stage="primary",
            row=row,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            provider=spec.provider,
            model=spec.model,
            request_id=row.primary_request_id,
            max_tokens=spec.max_output_tokens,
            temperature=preflight.temperature,
            classification=row.effective_classification,
            approval=approval,
            actor_id=actor_id,
        )
        evidence = self._provider_evidence(response)
        if self._is_approval_required(response):
            self._repository.mark_approval_required(
                row,
                stage="primary",
                approval_evidence=self._approval_evidence(response),
                runtime_policy=self._runtime_policy_evidence(response),
                provider_evidence=evidence,
                now=now,
            )
            await self._publish(
                "document.ai.approval_required",
                row=row,
                extra={"stage": "primary"},
            )
            return "approval_required"
        if response.status != "success":
            await self._fail_gateway(
                row=row,
                stage="primary",
                response=response,
                evidence=evidence,
                now=now,
            )
            return None
        try:
            parsed = DocumentAIResponseContract.parse_primary(
                response.content,
                offered_citation_ids=tuple(
                    citation.citation_id
                    for citation in preflight.context.manifest.citations
                ),
            )
        except DocumentAIAnalysisError as exc:
            self._repository.fail(
                row,
                error_code=exc.code,
                error_message=str(exc),
                error_details=exc.details,
                provider_evidence=evidence,
                stage="primary",
                now=now,
            )
            await self._publish(
                "document.ai.failed",
                row=row,
                extra={"stage": "primary", "error_code": exc.code},
            )
            return None
        self._repository.complete_primary(
            row,
            output_text=parsed.answer,
            output_text_sha256=_text_sha256(parsed.answer),
            citation_ids=parsed.citation_ids,
            provider_evidence=evidence,
            runtime_policy=self._runtime_policy_evidence(response),
            approval_evidence=self._approval_evidence(response),
            now=now,
        )
        await self._publish(
            "document.ai.primary_completed",
            row=row,
            extra={"citation_count": len(parsed.citation_ids)},
        )
        return None

    async def _execute_reviewer(
        self,
        *,
        row: DocumentAIAnalysisRunModel,
        spec: DocumentAIRequestSpec,
        preflight: DocumentAIPreflightResult,
        approval: DocumentAIApprovalCredentials | None,
        actor_id: str | None,
        now: datetime,
    ) -> str | None:
        if (
            row.content_state != "active"
            or not row.output_text
            or not row.output_text_sha256
            or _text_sha256(row.output_text) != row.output_text_sha256
        ):
            self._repository.fail(
                row,
                error_code="DOCUMENT_AI_PRIMARY_OUTPUT_UNAVAILABLE",
                error_message=(
                    "Validated primary output is unavailable for review."
                ),
                error_details={},
                provider_evidence=None,
                stage=None,
                now=now,
            )
            return None
        self._repeat_model_preflight(preflight.reviewer_model)
        citation_ids = tuple(row.output_citation_ids_json or [])
        system_prompt, user_prompt = build_reviewer_prompts(
            answer=row.output_text,
            citation_ids=citation_ids,
            packed_context=preflight.context.packed_context,
        )
        exact_tokens = (
            self._estimator.estimate(system_prompt)
            + self._estimator.estimate(user_prompt)
            + spec.reviewer_max_output_tokens
            + self.SAFETY_MARGIN_TOKENS
        )
        if exact_tokens > preflight.reviewer_model.context_window:
            self._repository.fail(
                row,
                error_code="DOCUMENT_AI_REVIEW_CONTEXT_LIMIT",
                error_message=(
                    "Exact reviewer context exceeds the model context window."
                ),
                error_details={
                    "planned_tokens": exact_tokens,
                    "context_window": (
                        preflight.reviewer_model.context_window
                    ),
                },
                provider_evidence=None,
                stage=None,
                now=now,
            )
            return None
        self._repository.start_stage(row, stage="reviewer", now=now)
        response = await self._gateway_call(
            stage="reviewer",
            row=row,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            provider=spec.reviewer_provider,
            model=spec.reviewer_model,
            request_id=row.reviewer_request_id,
            max_tokens=spec.reviewer_max_output_tokens,
            temperature=0.0,
            classification=row.effective_classification,
            approval=approval,
            actor_id=actor_id,
        )
        evidence = self._provider_evidence(response)
        if self._is_approval_required(response):
            self._repository.mark_approval_required(
                row,
                stage="reviewer",
                approval_evidence=self._approval_evidence(response),
                runtime_policy=self._runtime_policy_evidence(response),
                provider_evidence=evidence,
                now=now,
            )
            await self._publish(
                "document.ai.approval_required",
                row=row,
                extra={"stage": "reviewer"},
            )
            return "approval_required"
        if response.status != "success":
            await self._fail_gateway(
                row=row,
                stage="reviewer",
                response=response,
                evidence=evidence,
                now=now,
            )
            return None
        try:
            verdict = DocumentAIResponseContract.parse_reviewer(
                response.content,
                primary_citation_ids=citation_ids,
            )
        except DocumentAIAnalysisError as exc:
            self._repository.fail(
                row,
                error_code=exc.code,
                error_message=str(exc),
                error_details=exc.details,
                provider_evidence=evidence,
                stage="reviewer",
                now=now,
            )
            await self._publish(
                "document.ai.failed",
                row=row,
                extra={"stage": "reviewer", "error_code": exc.code},
            )
            return None
        self._repository.complete_reviewer(
            row,
            verdict=verdict.to_public_dict(),
            reviewer_response_sha256=_text_sha256(response.content),
            provider_evidence=evidence,
            runtime_policy=self._runtime_policy_evidence(response),
            approval_evidence=self._approval_evidence(response),
            approved=verdict.approved,
            now=now,
        )
        await self._publish(
            (
                "document.ai.completed"
                if verdict.approved
                else "document.ai.rejected"
            ),
            row=row,
            extra={
                "citation_coverage": verdict.citation_coverage.value,
                "policy_compliant": verdict.policy_compliant,
                "reason_codes": list(verdict.reason_codes),
            },
        )
        return None

    async def _gateway_call(
        self,
        *,
        stage: str,
        row: DocumentAIAnalysisRunModel,
        user_prompt: str,
        system_prompt: str,
        provider: str,
        model: str,
        request_id: str,
        max_tokens: int,
        temperature: float,
        classification: str,
        approval: DocumentAIApprovalCredentials | None,
        actor_id: str | None,
    ) -> GatewayResponse:
        try:
            return await self._gateway.ask(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                provider=provider,
                mode=f"document_{stage}",
                source="documents.ai",
                correlation_id=row.id,
                workspace_id=row.workspace_id,
                actor_id=actor_id,
                request_id=request_id,
                approval_id=(approval.approval_id if approval else None),
                approval_token=(approval.token if approval else None),
                temperature=temperature,
                max_tokens=max_tokens,
                data_classification=classification,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary is fail-closed
            return GatewayResponse(
                request_id=request_id,
                provider=provider,
                model=model,
                content="",
                status="error",
                error=self._gateway_exception_error(
                    provider=provider,
                    exception_type=exc.__class__.__name__,
                ),
            )

    @staticmethod
    def _gateway_exception_error(
        *, provider: str, exception_type: str
    ) -> GatewayError:
        return GatewayError(
            code="DOCUMENT_AI_GATEWAY_EXCEPTION",
            message="AI Gateway execution failed safely.",
            provider=provider,
            recoverable=True,
            details={"exception_type": exception_type},
        )

    async def _fail_gateway(
        self,
        *,
        row: DocumentAIAnalysisRunModel,
        stage: str,
        response: GatewayResponse,
        evidence: dict[str, Any],
        now: datetime,
    ) -> None:
        code = (
            response.error.code
            if response.error is not None
            else "DOCUMENT_AI_GATEWAY_FAILED"
        )
        self._repository.fail(
            row,
            error_code=code,
            error_message=f"Document AI {stage} Gateway request failed.",
            error_details={"stage": stage},
            provider_evidence=evidence,
            stage=stage,
            now=now,
        )
        await self._publish(
            "document.ai.failed",
            row=row,
            extra={"stage": stage, "error_code": code},
        )

    def _resolve_model(
        self,
        *,
        provider: str,
        model: str,
        requested_output_tokens: int,
    ) -> DocumentAIModelSnapshot:
        row = self._models.find_by_slug(model)
        if row is None or not row.enabled:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_UNAVAILABLE",
                "Selected document AI model is unavailable.",
                details={"provider": provider, "model": model},
            )
        if row.provider.casefold() != provider.casefold():
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_PROVIDER_MISMATCH",
                "Selected model does not belong to the explicit provider.",
                details={"provider": provider, "model": model},
            )
        if not row.supports_json:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_JSON_REQUIRED",
                "Document AI requires structured JSON model capability.",
                details={"provider": provider, "model": model},
            )
        if row.context_window is None or row.context_window < 1:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_CONTEXT_UNKNOWN",
                "Document AI requires a known positive context window.",
                details={"provider": provider, "model": model},
            )
        if (
            row.max_output_tokens is not None
            and requested_output_tokens > row.max_output_tokens
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_OUTPUT_LIMIT",
                "Requested output exceeds the model capability.",
                details={
                    "provider": provider,
                    "model": model,
                    "requested_output_tokens": requested_output_tokens,
                    "model_max_output_tokens": row.max_output_tokens,
                },
            )
        payload = {
            "provider": row.provider,
            "model": row.slug,
            "enabled": row.enabled,
            "context_window": row.context_window,
            "max_output_tokens": row.max_output_tokens,
            "supports_json": row.supports_json,
            "updated_at": row.updated_at.isoformat(),
        }
        return DocumentAIModelSnapshot(
            provider=row.provider,
            model=row.slug,
            context_window=row.context_window,
            max_output_tokens=(
                row.max_output_tokens or requested_output_tokens
            ),
            supports_json=row.supports_json,
            fingerprint=_canonical_sha256(payload),
        )

    def _repeat_model_preflight(
        self,
        expected: DocumentAIModelSnapshot,
    ) -> None:
        self._session.expire_all()
        current = self._resolve_model(
            provider=expected.provider,
            model=expected.model,
            requested_output_tokens=expected.max_output_tokens,
        )
        if current.fingerprint != expected.fingerprint:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_MODEL_PREFLIGHT_CHANGED",
                "Model capability changed after document AI preflight.",
                details={
                    "provider": expected.provider,
                    "model": expected.model,
                },
            )

    def _stage_policy(
        self,
        *,
        stage: str,
        provider: str,
        provider_trust: ProviderTrust,
        classification,
        workspace_id: str,
    ) -> DocumentAIStagePolicy:
        decision = self._runtime_policy.evaluate(
            RuntimePolicyContext(
                operation=PolicyOperation.MODEL_INFERENCE,
                data_classification=classification,
                workspace_id=workspace_id,
                provider_trust=provider_trust,
                contains_secrets=False,
                network_requested=False,
            )
        )
        return DocumentAIStagePolicy(
            stage=stage,
            provider=provider,
            provider_trust=provider_trust,
            decision=decision,
        )

    @staticmethod
    def _assert_policy_admission(preflight: DocumentAIPreflightResult) -> None:
        denied = [
            policy
            for policy in (
                preflight.primary_policy,
                preflight.reviewer_policy,
            )
            if policy.decision.action
            not in {PolicyAction.ALLOW, PolicyAction.REQUIRE_APPROVAL}
        ]
        if denied:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_POLICY_DENIED",
                "Runtime Policy denied a document AI route.",
                details={
                    "stages": [
                        {
                            "stage": item.stage,
                            "provider": item.provider,
                            "action": item.decision.action.value,
                            "reason_codes": list(
                                item.decision.reason_codes
                            ),
                        }
                        for item in denied
                    ]
                },
            )

    def _reviewer_preflight_tokens(
        self,
        *,
        packed_context: str,
        primary_max_output_tokens: int,
        reviewer_max_output_tokens: int,
    ) -> int:
        system_prompt, user_prompt = build_reviewer_prompts(
            answer="PRIMARY_ANSWER_PLACEHOLDER",
            citation_ids=("D1",),
            packed_context=packed_context,
        )
        return (
            self._estimator.estimate(system_prompt)
            + self._estimator.estimate(user_prompt)
            + (
                primary_max_output_tokens
                * self.REVIEWER_ANSWER_BYTES_PER_TOKEN
            )
            + reviewer_max_output_tokens
            + self.SAFETY_MARGIN_TOKENS
        )

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or len(value) < 8
            or len(value) > 128
            or any(
                character
                not in (
                    "abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789._:-"
                )
                for character in value
            )
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_IDEMPOTENCY_KEY_INVALID",
                "idempotency_key does not match the bounded safe contract.",
            )

    def _request_fingerprint(
        self,
        *,
        spec: DocumentAIRequestSpec,
        preflight: DocumentAIPreflightResult,
        retention_days: int,
    ) -> str:
        return _canonical_sha256(
            {
                "schema_version": "p3-001.5b-v1",
                "workspace_id": spec.workspace_id,
                "workflow": spec.workflow.value,
                "question_sha256": (
                    _text_sha256(spec.question.strip())
                    if spec.question is not None
                    else None
                ),
                "selection_fingerprint": spec.selection.fingerprint,
                "context_manifest_fingerprint": (
                    preflight.context.manifest.manifest_fingerprint
                ),
                "primary_model_fingerprint": (
                    preflight.primary_model.fingerprint
                ),
                "reviewer_model_fingerprint": (
                    preflight.reviewer_model.fingerprint
                ),
                "max_output_tokens": spec.max_output_tokens,
                "reviewer_max_output_tokens": (
                    spec.reviewer_max_output_tokens
                ),
                "retention_days": retention_days,
            }
        )

    @staticmethod
    def _assert_idempotent_match(
        *,
        row: DocumentAIAnalysisRunModel,
        request_fingerprint: str,
        preflight: DocumentAIPreflightResult,
    ) -> None:
        if row.request_fingerprint != request_fingerprint:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_IDEMPOTENCY_CONFLICT",
                "idempotency_key is already bound to a different request.",
                details={"analysis_run_id": row.id},
            )
        manifest = preflight.context.manifest
        if (
            row.context_sha256 != manifest.context_sha256
            or row.context_manifest_json.get("manifest_fingerprint")
            != manifest.manifest_fingerprint
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_CONTEXT_CHANGED",
                "Persisted source context changed after run creation.",
                details={"analysis_run_id": row.id},
            )

    def _result(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        created: bool,
        reused: bool,
    ) -> DocumentAIExecutionResult:
        return DocumentAIExecutionResult(
            record=self._record(row),
            created=created,
            reused=reused,
            approval_required=row.status in {
                "awaiting_primary_approval",
                "awaiting_reviewer_approval",
            },
            approval_stage=(
                "primary"
                if row.status == "awaiting_primary_approval"
                else (
                    "reviewer"
                    if row.status == "awaiting_reviewer_approval"
                    else None
                )
            ),
        )

    def _record(
        self,
        row: DocumentAIAnalysisRunModel,
    ) -> DocumentAIAnalysisRecord:
        citations = self._repository.list_citations(
            analysis_run_id=row.id,
            workspace_id=row.workspace_id,
            used_only=bool(row.output_citation_ids_json),
        )
        return DocumentAIAnalysisRecord(
            row=row,
            citations=tuple(
                self._citation_public(citation) for citation in citations
            ),
        )

    @staticmethod
    def _citation_public(
        citation: DocumentAIAnalysisCitationModel,
    ) -> dict[str, Any]:
        return {
            "citation_id": citation.citation_id,
            "document_id": citation.document_id,
            "run_id": citation.source_run_id,
            "source_kind": citation.source_kind,
            "source_id": citation.source_id,
            "source_ordinal": citation.source_ordinal,
            "classification": citation.classification,
            "source_text_sha256": citation.source_text_sha256,
            "fragment_text_sha256": citation.fragment_text_sha256,
            "fragment_index": citation.fragment_index,
            "fragment_count": citation.fragment_count,
            "character_start": citation.character_start,
            "character_end": citation.character_end,
            "estimated_tokens": citation.estimated_tokens,
            "locations": list(citation.locators_json or []),
            "injection_finding_codes": list(
                citation.injection_finding_codes_json or []
            ),
        }

    @staticmethod
    def _is_approval_required(response: GatewayResponse) -> bool:
        return bool(
            response.status == "error"
            and response.error is not None
            and response.error.code == "POLICY_APPROVAL_REQUIRED"
            and isinstance(response.metadata.get("policy_approval"), dict)
        )

    @staticmethod
    def _provider_evidence(response: GatewayResponse) -> dict[str, Any]:
        return {
            "request_id": response.request_id,
            "provider": response.provider,
            "model": response.model,
            "status": response.status,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            "latency_ms": response.latency_ms,
            "cost": response.cost,
            "error_code": (
                response.error.code if response.error is not None else None
            ),
            "error_recoverable": (
                response.error.recoverable
                if response.error is not None
                else None
            ),
        }

    @staticmethod
    def _runtime_policy_evidence(
        response: GatewayResponse,
    ) -> dict[str, Any]:
        value = response.metadata.get("runtime_policy")
        if not isinstance(value, dict):
            return {}
        allowed = {
            "policy_version",
            "action",
            "reason_codes",
            "fingerprint",
            "data_classification",
            "provider_trust",
        }
        return {key: value[key] for key in allowed if key in value}

    @staticmethod
    def _approval_evidence(response: GatewayResponse) -> dict[str, Any]:
        value = response.metadata.get("policy_approval")
        if not isinstance(value, dict):
            return {}
        allowed = {
            "approval_id",
            "status",
            "created",
            "request_id",
            "scope_fingerprint",
            "expires_at",
            "consumed_at",
        }
        return {key: value[key] for key in allowed if key in value}

    async def _publish(
        self,
        event_type: str,
        *,
        row: DocumentAIAnalysisRunModel,
        extra: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="documents.ai",
                workspace_id=row.workspace_id,
                correlation_id=row.id,
                payload={
                    "analysis_run_id": row.id,
                    "workflow": row.workflow,
                    "status": row.status,
                    "request_fingerprint": row.request_fingerprint,
                    "selection_fingerprint": row.selection_fingerprint,
                    "context_sha256": row.context_sha256,
                    "effective_classification": (
                        row.effective_classification
                    ),
                    "primary_provider": row.primary_provider,
                    "primary_model": row.primary_model,
                    "reviewer_provider": row.reviewer_provider,
                    "reviewer_model": row.reviewer_model,
                    **extra,
                },
            )
        )


__all__ = [
    "DocumentAIAnalysisRecord",
    "DocumentAIAnalysisService",
    "DocumentAIApprovalCredentials",
    "DocumentAIExecutionResult",
    "DocumentAIModelSnapshot",
    "DocumentAIPreflightResult",
    "DocumentAIRequestSpec",
    "DocumentAIRetentionPolicy",
    "DocumentAIStagePolicy",
]
