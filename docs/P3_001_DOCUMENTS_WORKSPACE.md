# P3-001 — Documents Workspace

## Статус

P3-001 начат с подэтапа **P3-001.1 Safe Document Intake Core**.

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

### P3-001.2 — Document Registry and Storage

- Alembic migration;
- Workspace-isolated document records;
- content-addressed managed storage;
- upload/list/get/delete API;
- persisted upload/delete events;
- удаление документа и всех связанных производных данных.

### P3-001.3 — Deterministic Text Extraction

- PDF, DOCX, XLSX и TXT loaders/parsers;
- page/sheet/paragraph provenance;
- bounded chunks;
- extraction status and failure evidence;
- без OCR fallback на этом этапе.

### P3-001.4 — OCR and Sensitive Derived Content

- isolated OCR execution;
- page image limits;
- OCR text inherits document classification;
- explicit retention and deletion semantics.

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
