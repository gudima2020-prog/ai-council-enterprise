# Document Security Policy

## Invariants

- All document access is scoped by both `workspace_id` and `document_id`.
- Derived units, chunks, OCR pages, context items, citations, and outputs inherit
  the source classification.
- Only explicitly selected persisted sources may enter an AI context. Never
  infer permission from an adjacent document, run, page, or chunk.
- Verify stored SHA-256 values before using source text.
- OCR must remain inside the trusted isolated runtime with network disabled.
- Raw bytes, extracted text, OCR text, prompts, and model responses do not
  belong in logs, errors, Event Bus payloads, or decision evidence.
- Document deletion and retention purge must remove or invalidate every derived
  content record while preserving only approved metadata evidence.

## AI context boundary

The required flow is:

```text
explicit source selection
-> Workspace/classification/hash validation
-> local prompt-injection scan
-> token/context budget preflight
-> inspectable context manifest and citations
-> external-provider warning
-> Runtime Policy and Human Control when required
-> AI Gateway request
-> citation/output validation
```

The local injection scanner is a warning layer, not proof that content is safe.
Retrieved text must always be delimited as untrusted data and must not override
system, Workspace, or user instructions.

Citations must identify the exact persisted source and a human-readable
location such as PDF page, OCR page, spreadsheet sheet/cell, DOCX paragraph or
table cell, or TXT document. A citation must never be fabricated when source
provenance is absent.

## Local-only capabilities

A local utility must declare and enforce:

- `execution = local`;
- `network_access = denied`;
- bounded memory, input size, output size, and filesystem scope;
- accepted classifications;
- persistence and retention behavior;
- audited implementation version.

Privacy tests must fail if a local-only operation attempts fetch, XHR,
WebSocket, beacon, provider calls, or other network access. Telemetry is
metadata-only and must not contain document input or output.
