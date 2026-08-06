from __future__ import annotations

import json

import pytest

from backend.documents.ai_analysis import (
    DocumentAIAnalysisError,
    DocumentAIResponseContract,
    DocumentAIReviewerCoverage,
    DocumentAIWorkflow,
    build_primary_prompts,
    build_reviewer_prompts,
)


def test_primary_response_accepts_only_offered_unique_citations() -> None:
    parsed = DocumentAIResponseContract.parse_primary(
        json.dumps(
            {
                "answer": "The contract expires in June.",
                "citation_ids": ["D2", "D1"],
            }
        ),
        offered_citation_ids=("D1", "D2", "D3"),
    )

    assert parsed.answer == "The contract expires in June."
    assert parsed.citation_ids == ("D2", "D1")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {"answer": "Unsupported.", "citation_ids": ["D999"]},
            "DOCUMENT_AI_CITATION_UNSUPPORTED",
        ),
        (
            {"answer": "No evidence.", "citation_ids": []},
            "DOCUMENT_AI_CITATION_REQUIRED",
        ),
        (
            {
                "answer": "Duplicate.",
                "citation_ids": ["D1", "D1"],
            },
            "DOCUMENT_AI_CITATION_DUPLICATE",
        ),
        (
            {
                "answer": "Extra field.",
                "citation_ids": ["D1"],
                "confidence": 1,
            },
            "DOCUMENT_AI_RESPONSE_SCHEMA_INVALID",
        ),
    ],
)
def test_primary_response_rejects_unverifiable_citations(
    payload: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(DocumentAIAnalysisError) as captured:
        DocumentAIResponseContract.parse_primary(
            json.dumps(payload),
            offered_citation_ids=("D1", "D2"),
        )

    assert captured.value.code == code
    assert "Unsupported." not in str(captured.value.details)


def test_primary_response_rejects_non_json_and_code_fences() -> None:
    for response in (
        "not json",
        '```json\n{"answer":"x","citation_ids":["D1"]}\n```',
        '[{"answer":"x","citation_ids":["D1"]}]',
    ):
        with pytest.raises(DocumentAIAnalysisError) as captured:
            DocumentAIResponseContract.parse_primary(
                response,
                offered_citation_ids=("D1",),
            )
        assert captured.value.code == "DOCUMENT_AI_RESPONSE_SCHEMA_INVALID"


def test_reviewer_verdict_requires_consistent_independent_approval() -> None:
    verdict = DocumentAIResponseContract.parse_reviewer(
        json.dumps(
            {
                "approved": True,
                "citation_coverage": "complete",
                "policy_compliant": True,
                "unsupported_citation_ids": [],
                "reason_codes": [],
            }
        ),
        primary_citation_ids=("D1", "D2"),
    )

    assert verdict.approved is True
    assert verdict.citation_coverage is DocumentAIReviewerCoverage.COMPLETE


@pytest.mark.parametrize(
    "payload",
    [
        {
            "approved": True,
            "citation_coverage": "partial",
            "policy_compliant": True,
            "unsupported_citation_ids": [],
            "reason_codes": [],
        },
        {
            "approved": True,
            "citation_coverage": "complete",
            "policy_compliant": False,
            "unsupported_citation_ids": [],
            "reason_codes": [],
        },
        {
            "approved": True,
            "citation_coverage": "complete",
            "policy_compliant": True,
            "unsupported_citation_ids": ["D999"],
            "reason_codes": [],
        },
    ],
)
def test_reviewer_verdict_rejects_contradictory_or_invented_evidence(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DocumentAIAnalysisError) as captured:
        DocumentAIResponseContract.parse_reviewer(
            json.dumps(payload),
            primary_citation_ids=("D1", "D2"),
        )

    assert captured.value.code == "DOCUMENT_AI_REVIEW_SCHEMA_INVALID"


def test_prompts_separate_trusted_question_and_untrusted_document_data() -> None:
    system_prompt, user_prompt = build_primary_prompts(
        workflow=DocumentAIWorkflow.QUESTION,
        question="When does the agreement end?",
        packed_context=(
            "SECURITY NOTICE\nBEGIN_UNTRUSTED_DOCUMENT_DATA\n{}\n"
            "END_UNTRUSTED_DOCUMENT_DATA"
        ),
    )

    assert "Output exactly one JSON object" in system_prompt
    assert "When does the agreement end?" in user_prompt
    assert "BEGIN_TRUSTED_USER_QUESTION" in user_prompt
    assert "BEGIN_UNTRUSTED_DOCUMENT_DATA" in user_prompt


def test_summary_rejects_question_and_question_workflow_requires_one() -> None:
    with pytest.raises(DocumentAIAnalysisError):
        build_primary_prompts(
            workflow=DocumentAIWorkflow.SUMMARY,
            question="This must not be accepted.",
            packed_context="context",
        )
    with pytest.raises(DocumentAIAnalysisError):
        build_primary_prompts(
            workflow=DocumentAIWorkflow.QUESTION,
            question=None,
            packed_context="context",
        )


def test_reviewer_prompt_contains_primary_result_but_no_new_citation_ids() -> None:
    system_prompt, user_prompt = build_reviewer_prompts(
        answer="A governed answer.",
        citation_ids=("D1",),
        packed_context="context with D1",
    )

    assert "independent reviewer" in system_prompt.lower()
    assert '"citation_ids":["D1"]' in user_prompt
    assert "context with D1" in user_prompt
