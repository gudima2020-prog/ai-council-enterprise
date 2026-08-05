# P3-001 — Documents Workspace

## Статус

P3-001 находится в активной разработке. Текущий рабочий подэтап —
**P3-001.4a Isolated PDF OCR Core**. Стабильной контрольной точкой проекта
остаётся `0.16.0 / P2-012` до завершения всего Documents Workspace release.

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

- Workspace-scoped OCR runs/pages;
- pending/running/completed/failed status и safe retry;
- explicit page selection и blank-page recommendation;
- sensitive derived-content classification и retention policy;
- bounded OCR page/result API;
- deletion of OCR text together with the source document;
- metadata-only audit/Event Bus evidence.

### P3-001.5 — Summaries, Questions and Citations

- AI Gateway only;
- explicit external-provider warning;
- chunked requests;
- page/sheet citations;
- Runtime Policy and approval enforcement.

### P3-001.6 — Documents UI and Release

- Workspace document registry;
- upload/delete;
- extraction/OCR status;
- preview and provenance;
- summary and question workflows;
- release verification.
