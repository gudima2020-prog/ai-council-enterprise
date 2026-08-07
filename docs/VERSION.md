# Version

Текущая версия: **0.17.0 / P3-001**
Статус: **стабильная контрольная точка**
Дата: **2026-08-07**
Alembic head: **`20260806_0057`**

## Основные возможности релиза

### Documents foundation

- safe intake PDF, DOCX, XLSX и TXT с bounded validation;
- Workspace-scoped registry и content-addressed managed storage;
- deterministic extraction с persisted units/chunks и provenance;
- isolated no-network PDF OCR с trusted immutable Docker image;
- classification-aware OCR/analysis retention и metadata-preserving purge.

### Governed Document AI

- explicit persisted source selection без implicit document context;
- conservative token budget, exact manifest/citations и prompt-injection
  warnings;
- explicit primary и independent reviewer models с capability preflight;
- effective source classification передаётся в общий AI Gateway;
- external-provider acknowledgement до отправки content;
- отдельные exact-scope one-time approvals для primary и reviewer;
- primary output выдаётся только после успешного reviewer verdict;
- persisted usage/cost/policy/approval evidence без raw prompts или tokens.

### Documents UI

- registry, upload/delete и Workspace isolation;
- extraction/OCR status, bounded OCR controls и exact local preview;
- provenance, SHA-256 и explicit source checkboxes;
- summary/question preflight, Runtime Policy decisions и approvals flow;
- completed/rejected/failed/purged states и explicit-content history;
- stale-request guards и exact Workspace/document/run validation для preview;
- `private, no-store` content responses и полная очистка one-time tokens;
- exact persisted identity и полный fragment SHA-256 в result citations;
- acknowledgement включает document context и primary answer, передаваемый
  внешнему reviewer.

## Проверенная конфигурация

- Alembic heads/current target: **`20260806_0057 (head)`**;
- P3-001.5b Documents/Gateway targeted suite: **216 passed, 1 warning**;
- backend regression на P3-001.5b checkpoint: **647 passed, 1 skipped,
  2 warnings**;
- local P3-001.6a review-closure targeted regression: **216 passed,
  1 warning**;
- local P3-001.6a full backend regression: **648 passed, 2 warnings**;
- frontend TypeScript/Vite production build с review-closure UI: **PASSED**;
- final Windows release gate после применения independent-review closure:
  **`verify_p3_001_6.bat`**.

Пропущенный тест связан с недоступностью symbolic links на целевой
Windows-конфигурации и не является отказом Documents functionality.

Известные неблокирующие предупреждения:

- Starlette TestClient использует deprecated `httpx` integration;
- example plugin создаёт duplicate OpenAPI Operation ID;
- Docker CLI без запущенного daemon пропускает только OCR image preflight;
  сам OCR runtime требует отдельно подготовленный trusted image.

## Release procedure

1. выполнить `verify_p3_001_6.bat` на release closure commit;
2. отправить `feature/p3-001`;
3. объединить ветку с `main`;
4. повторно выполнить `verify_p3_001_6.bat` на merge commit;
5. создать annotated tag `v0.17.0-p3-001`;
6. отправить `main` и tag в `origin`.
