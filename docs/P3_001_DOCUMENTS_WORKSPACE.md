# P3-001 — Documents Workspace

## Статус

P3-001 находится в активной разработке. Текущий реализованный подэтап —
**P3-001.5a Local AI Context and Citation Safety Core**. Стабильной контрольной
точкой проекта остаётся `0.16.0 / P2-012` до завершения всего Documents
Workspace release.

Documents Workspace должен поддерживать PDF, DOCX, XLSX и TXT, извлечение
текста и передачу только явно выбранного контента в AI Gateway. OCR, таблицы,
поиск, сравнение, извлечение реквизитов и локальная база знаний подключаются
отдельными подэтапами.

## P3-001.1 — Safe Document Intake Core

Первый подэтап не сохраняет файлы и не добавляет HTTP upload endpoint. Он
создаёт независимый от БД security boundary, который должен пройти каждый
документ до persistence и parsing.

Реализуемые инварианты:

- allowlist форматов: PDF, DOCX, XLSX, TXT;
- проверка расширения, declared MIME и сигнатуры содержимого;
- generic `application/octet-stream` допускается только с последующей
  проверкой фактического формата;
- bounded stream read с default limit 25 MiB;
- Unicode-safe filename normalization;
- path traversal, control characters и Windows reserved names отклоняются;
- SHA-256 содержимого и deterministic Workspace-scoped intake fingerprint;
- descriptor содержит только метаданные и не удерживает raw bytes;
- classification берётся из общего `DataClassification`;
- DOCX/XLSX проверяются как OOXML ZIP без извлечения на диск;
- archive traversal, duplicate entries, symlinks, encryption, macros,
  excessive entry count, uncompressed size и compression ratio блокируются;
- TXT проверяется как текст и получает encoding marker;
- стабильные machine-readable error codes.

## Архитектурные границы

Структура Documents Module остаётся совместимой с общей архитектурой:

```text
backend/documents/
├── intake.py
├── loaders/
├── parsers/
├── ocr/
├── chunking/
├── embeddings/
├── search/
└── analysis/
```

В P3-001.1 создаётся только intake core. Он не:

- записывает документы в SQLite или файловое хранилище;
- распаковывает OOXML на диск;
- выполняет PDF/DOCX/XLSX parsing;
- запускает OCR;
- отправляет содержимое в LLM;
- создаёт embeddings;
- логирует raw filename или content в evidence payload.

## План следующих подэтапов

## P3-001.2a — Document Registry and Managed Storage Core

Реализованы:

- Alembic revision `20260804_0054`;
- Workspace-isolated registry с active/deleted tombstone lifecycle;
- content-addressed managed storage по SHA-256;
- atomic temporary write + `os.replace`;
- verified read и fail-closed detection повреждённых blobs;
- дедупликация blobs между Workspace при раздельных registry records;
- безопасное удаление blob только после последней active-ссылки;
- восстановление ранее удалённого exact document scope;
- append-only SHA-256 chain событий `document.uploaded` и
  `document.deleted` без raw bytes/content;
- in-process Event Bus publication с безопасным payload;
- internal upload/list/get/read/delete service contract.

HTTP multipart upload, authentication/permissions и публичные API routes
переносятся в P3-001.2b. Это не требует `python-multipart` в persistence
core и сохраняет независимость storage boundary от FastAPI.

## P3-001.2b — Document Registry REST API

Реализованы:

- Workspace-scoped multipart upload для PDF, DOCX, XLSX и TXT;
- серверное назначение classification из effective Workspace Policy;
- authenticated Human Control actor binding без клиентского `actor_id`;
- отдельные permissions `document.view`, `document.upload`,
  `document.delete` и `document.audit`;
- bounded multipart read до передачи bytes в intake/storage core;
- JSON-object metadata form с запретом raw content/secret fields;
- list/get/delete/events routes;
- idempotent upload и delete semantics;
- безопасное HTTP error mapping без раскрытия storage paths;
- OpenAPI и end-to-end API tests.

Публичный API не возвращает `storage_key`, raw bytes или извлечённый текст.
Deleted tombstones и event evidence остаются внутри registry/audit boundary.

## P3-001.3a — Deterministic Text Extraction Core

Реализуются независимые от БД loaders/parsers:

- PDF через `pypdf` с page provenance;
- DOCX через `python-docx` с paragraph/table-cell provenance;
- XLSX через `openpyxl` с sheet/cell provenance;
- TXT через intake encoding marker;
- NFKC и стабильная нормализация переводов строк;
- SHA-256 каждого unit и всего канонического extracted text;
- deterministic parser version;
- fail-closed limits по pages, paragraphs, tables, rows, cells, units и chars;
- формулы XLSX сохраняются как формулы и не пересчитываются;
- OCR, LLM, embeddings и persistence не запускаются.

## P3-001.3b — Extraction Persistence and API

Реализованы:

- Alembic revision `20260805_0055`;
- Workspace-isolated extraction runs со статусами
  `pending`, `running`, `completed`, `failed`;
- idempotent exact-run key по document, source SHA-256 и parser version;
- безопасный retry failed run с увеличением `attempt_count`;
- persisted extraction units с page/paragraph/table-cell/sheet/cell provenance;
- deterministic overlapping chunks с source unit ordinals;
- SHA-256 extracted text, units и chunks;
- bounded list endpoints для runs, units и chunks;
- permission `document.extract` и authenticated actor binding;
- persisted failure evidence без raw bytes или extracted text;
- metadata-only Event Bus события `document.extraction.completed` и
  `document.extraction.failed`;
- удаление runs, units и chunks при tombstone deletion документа;
- OpenAPI, persistence, isolation, retry и cleanup tests.

OCR, embeddings и отправка extracted text в LLM на этом этапе не выполняются.

### P3-001.4a — Isolated PDF OCR Core

Реализованы:

- PDF OCR только через отдельный Docker runtime;
- trusted runtime image label и исполнение по immutable image ID;
- `network=none`, read-only root/input, capability drop,
  `no-new-privileges`, CPU/RAM/PID/time limits;
- PDFium rendering и Tesseract OCR внутри контейнера, а не в API process;
- English/Russian language packs с безопасным allowlist форматом;
- явный 1-based page selection, сортировка и дедупликация страниц;
- fail-closed limits по PDF pages, selected pages, page/total pixels,
  PNG bytes, page/total text characters и runtime timeout;
- page images существуют только в container tmpfs и удаляются до выхода;
- временная host-копия исходного PDF удаляется после runtime call;
- OCR text наследует classification исходного документа;
- SHA-256 source PDF, page PNG, page text и канонического OCR text;
- metadata-only errors без raw PDF, page image или OCR text;
- host-side строгая проверка типов, image ID/version, error/warning allowlist,
  runtime metadata и limits;
- build/smoke script `prepare_p3_001_4a_ocr_runtime.bat`;
- deterministic core и Docker protocol tests.

Runtime настраивается через `AI_STUDIO_OCR_ENABLED`,
`AI_STUDIO_OCR_IMAGE`, опциональные `AI_STUDIO_DOCKER_EXECUTABLE` и
`AI_STUDIO_OCR_TEMP_ROOT`. Образ должен иметь exact labels
`org.ai-studio.ocr-runtime=p3-001.4a` и
`org.ai-studio.ocr-version=5-v1`; исполнение выполняется по проверенному
`sha256:<64 hex>` image ID.

Проверка core от 2026-08-05:

- OCR targeted tests: **20 passed**;
- Documents Workspace targeted tests: **99 passed**;
- full backend regression: **553 passed, 2 known warnings**;
- frontend TypeScript/Vite production build: **PASSED**;
- synthetic PDFium/Tesseract runner smoke test: **PASSED**.

Сборка и smoke test именно Docker-образа выполняются на целевом Windows-host
через `prepare_p3_001_4a_ocr_runtime.bat`, поскольку Docker daemon не входил
в среду core-проверки.

OCR persistence, API routes и retention records на этом подэтапе не
создаются.

### P3-001.4b — OCR Persistence, Retention and API

Реализованы:

- Alembic revision `20260805_0056` и отдельные таблицы
  `document_ocr_runs` / `document_ocr_pages`;
- Workspace-scoped OCR runs/pages со статусами `pending`, `running`,
  `completed`, `failed`;
- deterministic request fingerprint по OCR policy, page selection и retention;
- idempotent reuse completed run, конфликт для running и безопасный retry
  failed/purged run в той же записи с увеличением `attempt_count`;
- explicit 1-based page selection с сортировкой/дедупликацией и режимом `all`;
- persisted page text, text/PNG SHA-256, размеры, warnings и runtime provenance
  без сохранения page images;
- blank-page numbers и machine-readable рекомендация
  `review_or_retry_blank_pages`;
- наследование source classification каждым run/page;
- classification-aware policy `classification-retention-v1`: public 365,
  internal 180, confidential 90, restricted 30 дней; API может только сократить,
  но не продлить соответствующий предел;
- retention expiry, `active` / `purged` text state и metadata-preserving purge;
- отдельный permission `document.ocr` для запуска и
  `human_control.retention.manage` для принудительного purge expired text;
- bounded Workspace API:
  `POST/GET /api/workspaces/{workspace_id}/documents/{document_id}/ocr-runs`,
  `GET .../ocr-runs/{run_id}`, `GET .../ocr-runs/{run_id}/pages` и
  `POST .../ocr-retention/purge`;
- OCR page text возвращается только при явном `include_text=true`;
- безопасное HTTP error mapping и persisted error evidence без PDF/OCR text;
- metadata-only Event Bus события `document.ocr.completed`,
  `document.ocr.failed` и `document.ocr.retention_purged`;
- удаление OCR pages/runs вместе с extraction artifacts до tombstone deletion
  исходного документа;
- migration, persistence, isolation, permission, retry, retention, cleanup,
  OpenAPI и regression tests;
- Windows verification script `verify_p3_001_4b.bat`.

OCR runtime остаётся изолированным boundary подэтапа P3-001.4a. P3-001.4b
не передаёт OCR text в AI Gateway и не создаёт embeddings.

Проверка P3-001.4b от 2026-08-05:

- OCR persistence/API tests: **19 passed**;
- Documents Workspace targeted suite: **118 passed**;
- migration manager: **9 passed**;
- verification script aggregate: **127 passed, 1 warning**;
- full backend regression: **571 passed, 1 skipped, 2 known warnings**;
- frontend TypeScript/Vite production build: **PASSED**.

### P3-001.5a — Local AI Context and Citation Safety Core

Реализованы независимые от БД, HTTP и provider SDK контракты:

- обязательный explicit selection manifest с точной идентичностью
  document/run/source kind/source record;
- fail-closed Workspace validation и проверка persisted SHA-256 каждого
  выбранного extracted chunk/unit или OCR page;
- effective classification как наиболее строгая classification всех sources;
- typed citations для PDF/OCR page, DOCX paragraph/table cell, XLSX
  sheet/cell и TXT document с persisted source identity и fragment hashes;
- deterministic fragments до 4 000 символов без молчаливого пропуска
  выбранного текста;
- JSON data envelope с обязательным указанием, что document content является
  недоверенными данными, а не инструкциями;
- model-agnostic `utf8-byte-conservative-v1` token estimate и прозрачный budget
  по system prompt, conversation, user prompt, document context, reserved
  output и safety margin;
- fail-closed context-window preflight без raw content в error details;
- bounded local prompt-injection pattern scan для English/Russian с режимами
  `warn` и `block`, SHA-256 matched fragments и без заявления, что отсутствие
  совпадений доказывает безопасность;
- metadata-only public manifest, context SHA-256, selection fingerprint и
  manifest fingerprint;
- repository-level `AGENTS.md`, progressive-disclosure development/security/
  migration/release policies и архитектурный план P3-002/P3-003;
- Windows verification script `verify_p3_001_5a.bat`.

Token estimate намеренно консервативен и не заменяет tokenizer конкретного
provider/model. В P3-001.5b AI Gateway обязан повторно проверить model
capability/context limit перед отправкой.

P3-001.5a не читает БД, не сохраняет manifest, не создаёт HTTP routes, не
вызывает LLM и не считает model cost. Эти действия относятся к следующему
подэтапу и не могут обходить Runtime Policy.

Локальная core-проверка от 2026-08-05:

- P3-001.5a contract tests: **38 passed**;
- Documents Workspace targeted regression: **165 passed, 1 known warning**.

Полный backend regression и frontend build должны быть подтверждены на
целевом Windows-host скриптом `verify_p3_001_5a.bat` до staging/commit.

### P3-001.5b — Gateway Summaries, Questions and Citations

Реализован governed pipeline поверх P3-001.5a:

- Workspace-scoped resolver читает только точные явно выбранные persisted
  extraction units/chunks и OCR pages, проверяет active document binding,
  run state, OCR retention, provenance, character count и SHA-256;
- migration `20260806_0057` сохраняет idempotent analysis runs и отдельные
  typed citation evidence rows; question/answer хранятся только в content
  columns, а Event Bus, errors и audit evidence остаются metadata-only;
- bounded `/document-ai/preflight` и `/document-ai/runs` API поддерживает
  summary/question, list/get, explicit content retrieval и retention purge;
- разрешены только explicit provider/model routes; primary и independent
  reviewer обязаны использовать разные models с `supports_json`, известным
  context window и допустимым output limit;
- model capability/context preflight повторяется непосредственно перед каждым
  Gateway call; effective source classification передаётся в Gateway как
  non-downgrade override Workspace classification;
- external provider warning возвращается до запроса, а execution требует
  явного acknowledgement;
- Runtime Policy выполняется до вызова и повторно в AI Gateway; confidential
  trusted-external primary/reviewer используют разные exact-scope request IDs
  и отдельные one-time Human Control approvals;
- primary response допускает только strict JSON `answer/citation_ids`, требует
  хотя бы одну persisted citation и отвергает duplicate, invented или
  unsupported identifiers;
- отдельный reviewer Gateway request проверяет citation coverage и policy
  compliance; непротиворечивый approval обязателен для completed status, а
  primary output не выдаётся через API до успешного reviewer verdict;
- persisted provider evidence включает usage, latency, cost, error code,
  Runtime Policy и approval metadata, но никогда approval token или raw prompt;
- classification retention очищает question/answer, сохраняя hashes, manifest,
  citations и decision evidence; удаление любого исходного документа удаляет
  весь зависимый multi-document analysis run.

Локальная проверка до Windows release gate:

- P3-001.5b core/resolver/persistence/service/API/Gateway contracts: PASSED;
- Documents/Gateway targeted regression: **216 passed, 1 known warning**;
- migration round-trip to head `20260806_0057`: PASSED.

Полный backend regression и frontend build должны быть подтверждены на
целевом Windows-host скриптом `verify_p3_001_5b.bat` до staging/commit.

### P3-001.6 — Documents UI and Release

- Workspace document registry;
- upload/delete;
- extraction/OCR status;
- preview and provenance;
- summary and question workflows;
- release verification.
