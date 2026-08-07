from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.documents.ai_context import DocumentContextCitation
from backend.documents.models import (
    DocumentAIAnalysisCitationModel,
    DocumentAIAnalysisRunModel,
)

_BLOCKED_EVIDENCE_KEYS = {
    "answer",
    "content",
    "document_content",
    "output",
    "prompt",
    "question",
    "raw_content",
    "response",
    "secret",
    "text",
    "token",
}


def _assert_metadata_only(value: Any, path: str = "evidence") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _BLOCKED_EVIDENCE_KEYS:
                raise ValueError(f"Unsafe document AI evidence key: {path}.{key}")
            _assert_metadata_only(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_metadata_only(child, f"{path}[{index}]")


class DocumentAIAnalysisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_pending(
        self,
        *,
        workspace_id: str,
        workflow: str,
        idempotency_key: str,
        request_fingerprint: str,
        selection_fingerprint: str,
        selected_sources: list[dict[str, Any]],
        context_manifest: dict[str, Any],
        context_sha256: str,
        effective_classification: str,
        primary_provider: str,
        primary_model: str,
        primary_request_id: str,
        reviewer_provider: str,
        reviewer_model: str,
        reviewer_request_id: str,
        provider_trust: dict[str, str],
        external_provider_acknowledged: bool,
        request_text: str | None,
        request_text_sha256: str | None,
        retention_policy: str,
        retention_days: int,
        retention_expires_at: datetime,
        requested_by: str | None,
        citations: tuple[DocumentContextCitation, ...],
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        for value in (
            selected_sources,
            context_manifest,
            provider_trust,
        ):
            _assert_metadata_only(value)
        row = DocumentAIAnalysisRunModel(
            workspace_id=workspace_id,
            workflow=workflow,
            status="pending",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            selection_fingerprint=selection_fingerprint,
            selected_sources_json=[dict(item) for item in selected_sources],
            context_manifest_json=dict(context_manifest),
            context_sha256=context_sha256,
            effective_classification=effective_classification,
            primary_provider=primary_provider,
            primary_model=primary_model,
            primary_request_id=primary_request_id,
            reviewer_provider=reviewer_provider,
            reviewer_model=reviewer_model,
            reviewer_request_id=reviewer_request_id,
            provider_trust_json=dict(provider_trust),
            external_provider_acknowledged=(
                external_provider_acknowledged
            ),
            request_text=request_text,
            request_text_sha256=request_text_sha256,
            output_text=None,
            output_text_sha256=None,
            output_citation_ids_json=[],
            content_state="active",
            content_purged_at=None,
            reviewer_verdict_json={},
            reviewer_response_sha256=None,
            runtime_policy_json={},
            approval_evidence_json={},
            provider_evidence_json={},
            error_code=None,
            error_message=None,
            error_details_json={},
            retention_policy=retention_policy,
            retention_days=retention_days,
            retention_expires_at=retention_expires_at,
            requested_by=requested_by,
            attempt_count=0,
            started_at=None,
            completed_at=None,
            failed_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        for citation in citations:
            self._session.add(
                DocumentAIAnalysisCitationModel(
                    analysis_run_id=row.id,
                    workspace_id=workspace_id,
                    citation_id=citation.citation_id,
                    citation_ordinal=int(citation.citation_id[1:]),
                    document_id=citation.document_id,
                    source_run_id=citation.run_id,
                    source_kind=citation.source_kind.value,
                    source_id=citation.source_id,
                    source_ordinal=citation.source_ordinal,
                    classification=citation.classification.value,
                    source_text_sha256=citation.source_text_sha256,
                    fragment_text_sha256=citation.fragment_text_sha256,
                    fragment_index=citation.fragment_index,
                    fragment_count=citation.fragment_count,
                    character_start=citation.character_start,
                    character_end=citation.character_end,
                    estimated_tokens=citation.estimated_tokens,
                    locators_json=[
                        locator.to_public_dict()
                        for locator in citation.locators
                    ],
                    injection_finding_codes_json=list(
                        citation.injection_finding_codes
                    ),
                    used_in_output=False,
                    created_at=now,
                )
            )
        self._session.flush()
        return row

    def find_idempotency(
        self,
        *,
        workspace_id: str,
        idempotency_key: str,
    ) -> DocumentAIAnalysisRunModel | None:
        return self._session.scalar(
            select(DocumentAIAnalysisRunModel).where(
                DocumentAIAnalysisRunModel.workspace_id == workspace_id,
                DocumentAIAnalysisRunModel.idempotency_key == idempotency_key,
            )
        )

    def get(
        self,
        *,
        analysis_run_id: str,
        workspace_id: str,
    ) -> DocumentAIAnalysisRunModel | None:
        return self._session.scalar(
            select(DocumentAIAnalysisRunModel).where(
                DocumentAIAnalysisRunModel.id == analysis_run_id,
                DocumentAIAnalysisRunModel.workspace_id == workspace_id,
            )
        )

    def list_runs(
        self,
        *,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentAIAnalysisRunModel]:
        return list(
            self._session.scalars(
                select(DocumentAIAnalysisRunModel)
                .where(DocumentAIAnalysisRunModel.workspace_id == workspace_id)
                .order_by(
                    DocumentAIAnalysisRunModel.created_at.desc(),
                    DocumentAIAnalysisRunModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def list_citations(
        self,
        *,
        analysis_run_id: str,
        workspace_id: str,
        used_only: bool = False,
    ) -> list[DocumentAIAnalysisCitationModel]:
        statement = select(DocumentAIAnalysisCitationModel).where(
            DocumentAIAnalysisCitationModel.analysis_run_id == analysis_run_id,
            DocumentAIAnalysisCitationModel.workspace_id == workspace_id,
        )
        if used_only:
            statement = statement.where(
                DocumentAIAnalysisCitationModel.used_in_output.is_(True)
            )
        return list(
            self._session.scalars(
                statement.order_by(
                    DocumentAIAnalysisCitationModel.citation_ordinal.asc()
                )
            ).all()
        )

    def start_stage(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        stage: str,
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        if stage not in {"primary", "reviewer"}:
            raise ValueError("stage must be primary or reviewer")
        row.status = f"{stage}_running"
        if stage == "primary":
            row.attempt_count += 1
            row.started_at = now
            row.completed_at = None
            row.failed_at = None
            row.error_code = None
            row.error_message = None
            row.error_details_json = {}
            row.content_state = "active"
            row.content_purged_at = None
        row.updated_at = now
        self._session.flush()
        return row

    def mark_approval_required(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        stage: str,
        approval_evidence: dict[str, Any],
        runtime_policy: dict[str, Any],
        provider_evidence: dict[str, Any],
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        if stage not in {"primary", "reviewer"}:
            raise ValueError("stage must be primary or reviewer")
        for value in (
            approval_evidence,
            runtime_policy,
            provider_evidence,
        ):
            _assert_metadata_only(value)
        row.status = f"awaiting_{stage}_approval"
        row.approval_evidence_json = {
            **dict(row.approval_evidence_json or {}),
            stage: dict(approval_evidence),
        }
        row.runtime_policy_json = {
            **dict(row.runtime_policy_json or {}),
            stage: dict(runtime_policy),
        }
        row.provider_evidence_json = {
            **dict(row.provider_evidence_json or {}),
            stage: dict(provider_evidence),
        }
        row.updated_at = now
        self._session.flush()
        return row

    def complete_primary(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        output_text: str,
        output_text_sha256: str,
        citation_ids: tuple[str, ...],
        provider_evidence: dict[str, Any],
        runtime_policy: dict[str, Any],
        approval_evidence: dict[str, Any],
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        for value in (
            provider_evidence,
            runtime_policy,
            approval_evidence,
        ):
            _assert_metadata_only(value)
        citations = self.list_citations(
            analysis_run_id=row.id,
            workspace_id=row.workspace_id,
        )
        offered = {citation.citation_id for citation in citations}
        if (
            not citation_ids
            or len(citation_ids) != len(set(citation_ids))
            or any(value not in offered for value in citation_ids)
        ):
            raise ValueError("citation_ids must be unique persisted citations")
        used = set(citation_ids)
        for citation in citations:
            citation.used_in_output = citation.citation_id in used
        row.status = "awaiting_reviewer"
        row.output_text = output_text
        row.output_text_sha256 = output_text_sha256
        row.output_citation_ids_json = list(citation_ids)
        row.provider_evidence_json = {
            **dict(row.provider_evidence_json or {}),
            "primary": dict(provider_evidence),
        }
        row.runtime_policy_json = {
            **dict(row.runtime_policy_json or {}),
            "primary": dict(runtime_policy),
        }
        if approval_evidence:
            row.approval_evidence_json = {
                **dict(row.approval_evidence_json or {}),
                "primary": dict(approval_evidence),
            }
        row.updated_at = now
        self._session.flush()
        return row

    def complete_reviewer(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        verdict: dict[str, Any],
        reviewer_response_sha256: str,
        provider_evidence: dict[str, Any],
        runtime_policy: dict[str, Any],
        approval_evidence: dict[str, Any],
        approved: bool,
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        for value in (
            verdict,
            provider_evidence,
            runtime_policy,
            approval_evidence,
        ):
            _assert_metadata_only(value)
        row.status = "completed" if approved else "rejected"
        row.reviewer_verdict_json = dict(verdict)
        row.reviewer_response_sha256 = reviewer_response_sha256
        row.provider_evidence_json = {
            **dict(row.provider_evidence_json or {}),
            "reviewer": dict(provider_evidence),
        }
        row.runtime_policy_json = {
            **dict(row.runtime_policy_json or {}),
            "reviewer": dict(runtime_policy),
        }
        if approval_evidence:
            row.approval_evidence_json = {
                **dict(row.approval_evidence_json or {}),
                "reviewer": dict(approval_evidence),
            }
        row.completed_at = now if approved else None
        row.failed_at = None if approved else now
        if not approved:
            row.error_code = "DOCUMENT_AI_REVIEW_REJECTED"
            row.error_message = "Independent reviewer rejected the answer."
            row.error_details_json = {
                "reason_codes": list(verdict.get("reason_codes", [])),
            }
        row.updated_at = now
        self._session.flush()
        return row

    def fail(
        self,
        row: DocumentAIAnalysisRunModel,
        *,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any],
        provider_evidence: dict[str, Any] | None,
        stage: str | None,
        now: datetime,
    ) -> DocumentAIAnalysisRunModel:
        _assert_metadata_only(error_details)
        if provider_evidence is not None:
            _assert_metadata_only(provider_evidence)
        row.status = "failed"
        row.error_code = error_code
        row.error_message = error_message
        row.error_details_json = dict(error_details)
        if stage is not None and provider_evidence is not None:
            row.provider_evidence_json = {
                **dict(row.provider_evidence_json or {}),
                stage: dict(provider_evidence),
            }
        row.failed_at = now
        row.completed_at = None
        row.updated_at = now
        self._session.flush()
        return row

    def purge_expired(
        self,
        *,
        workspace_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        rows = list(
            self._session.scalars(
                select(DocumentAIAnalysisRunModel).where(
                    DocumentAIAnalysisRunModel.workspace_id == workspace_id,
                    DocumentAIAnalysisRunModel.content_state == "active",
                    DocumentAIAnalysisRunModel.retention_expires_at <= now,
                )
            ).all()
        )
        for row in rows:
            row.request_text = None
            row.output_text = None
            row.content_state = "purged"
            row.content_purged_at = now
            row.updated_at = now
        self._session.flush()
        return {
            "runs": len(rows),
            "run_ids": [row.id for row in rows],
        }

    def delete_derived(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> dict[str, int]:
        run_ids = list(
            self._session.scalars(
                select(DocumentAIAnalysisCitationModel.analysis_run_id)
                .where(
                    DocumentAIAnalysisCitationModel.document_id == document_id,
                    DocumentAIAnalysisCitationModel.workspace_id == workspace_id,
                )
                .distinct()
            ).all()
        )
        if not run_ids:
            return {"ai_runs": 0, "ai_citations": 0}
        citations = self._session.execute(
            delete(DocumentAIAnalysisCitationModel).where(
                DocumentAIAnalysisCitationModel.analysis_run_id.in_(run_ids)
            )
        ).rowcount or 0
        runs = self._session.execute(
            delete(DocumentAIAnalysisRunModel).where(
                DocumentAIAnalysisRunModel.id.in_(run_ids),
                DocumentAIAnalysisRunModel.workspace_id == workspace_id,
            )
        ).rowcount or 0
        self._session.flush()
        return {
            "ai_runs": int(runs),
            "ai_citations": int(citations),
        }


__all__ = ["DocumentAIAnalysisRepository"]
