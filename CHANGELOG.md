# Changelog

## Unreleased — P3-002 Governed Developer Agent Profiles

- Added immutable, bounded and SHA-256-fingerprinted Agent Policy Profile
  contracts with deterministic `allow`, `require_approval` and `deny`
  decisions that cannot replace canonical platform policy.
- Bound every allowed tool to immutable required capabilities so omitted
  request metadata cannot bypass filesystem, network or approval controls.
- Added six conservative built-in profiles for development, research,
  document analysis, repository review, release and production operation.
- Added a bounded content-free Context Checkpoint contract with repository-path,
  Git SHA, schema and envelope-integrity validation.
- Added P3-002.1a contract tests and `verify_p3_002_1a.bat`.
- Added P3-002.1b Workspace-scoped immutable custom profile versions, exact
  fingerprint-bound active selection, trusted tool-capability validation and
  Human Control-bound REST API with migration `20260807_0058`.
- Added P3-002.2a pure Before Tool Execution composition with fail-closed
  required-layer checks, exact profile fingerprint binding, canonical
  Runtime Policy isolation preservation and trusted Human Control evidence.
- Added P3-002.2b-A trusted enforcement adapters with explicit governed ToolDefinition binding, canonical capability drift rejection, Workspace/Runtime evidence fingerprints and non-bypass composition regression.
- Exact-scope Human Control approval, ToolExecutionRuntime wiring, Event Bus evidence and After Tool Execution enforcement remain deferred.

## v0.17.0 — P3-001 Documents Workspace — 2026-08-07

- Added safe PDF/DOCX/XLSX/TXT intake, Workspace registry and content-addressed managed storage.
- Added deterministic extraction with persisted units/chunks, provenance and document-bound cleanup.
- Added trusted isolated PDF OCR with no-network Docker execution, page evidence and classification-aware retention.
- Added explicit persisted-source context, conservative token budget, typed citations and prompt-injection warnings.
- Added governed summary/question execution through the shared AI Gateway with external-provider acknowledgement.
- Added independent reviewer enforcement and separate exact-scope one-time approvals for primary and reviewer stages.
- Added persisted Document AI evidence, content retention/purge and fail-closed output gating until reviewer approval.
- Added React Documents Workspace for registry, upload/delete, extraction/OCR, exact preview, source selection and history.
- Closed the independent release review with stale-request guards, private no-store content responses, exact citation identities, complete external-reviewer disclosure and failed-run envelope handling.
- Added `verify_p3_001_6.bat`; Alembic head is `20260806_0057`.

## v0.16.0 — P2-012 Policy Approval Workflow & Decision Evidence — 2026-08-04

- Added Workspace-scoped exact-subject Runtime Policy approvals and append-only decision evidence.
- Added one-time domain-hashed capability tokens with atomic consume and bounded TTL.
- Enforced approvals for AI Gateway routes and runtime artifact export without failover or TOCTOU bypass.
- Added React Approval Center with queue, exact scope, reason codes and approve/deny/revoke actions.
- Added `verify_p2_012.bat`; Alembic head was `20260731_0053`.

## v0.15.0 — P2-011 Runtime Policy, Trust and Data Classification — 2026-07-30

- Added deterministic fail-closed policy core with reason codes and SHA-256 fingerprints.
- Added Workspace-scoped data classification and provider trust persistence/API.
- Enforced policy on every AI Gateway primary and failover route before adapter execution and secret injection.
- Added host metadata-validation and isolated-container runtime trust boundaries.
- Added fail-closed runtime-policy resolution and policy snapshots in runtime metadata/events.
- Restricted artifact export is denied; confidential export requires approval; policy is rechecked before ZIP download.
- Added React Workspace Policy UI with provider trust controls, warnings, save and reset flows.
- Added `verify_p2_011.bat`; full backend regression and frontend production build pass.
- Alembic head remains `20260724_0052`; no schema migration is required.

## v0.14.0 — P2-010 Docker Isolated Runtime — 2026-07-27

- Docker-backed verification boundary for untrusted pytest/build execution.
- `network=none`, read-only source/rootfs, dropped capabilities, no-new-privileges and CPU/RAM/PID/time limits.
- Runtime audit table, status/run APIs and safe artifact ZIP export.
- Explicit runtime-image preparation; no image pulls during execution.
- Tracked sensitive files block runtime execution before a container is created.


## v0.13.0 — P2-009 Multi-Provider Model Gateway — 2026-07-24

- generic OpenAI-compatible provider adapter and SVRTR integration;
- persisted provider health, EMA latency and circuit breaker;
- explicit provider failover with streaming safety;
- health-aware routing by cost / latency / reliability / quality;
- SVRTR model catalog/pricing metadata with temporary-price expiry guard;
- Alembic head `20260724_0051`.

## v0.12.0 / P2-008 — Council-to-Sandbox Agent Execution

- Добавлен capability-limited Code Agent поверх P2-007 Code Sandbox.
- Поддержан handoff сохранённого `council_run_id` в coding execution.
- Агент не получает shell, network, secrets или произвольный filesystem access.
- Изменения ограничены точным `writable_paths` allowlist и применяются только в Git worktree.
- Structured JSON edit plan поддерживает full-file `write`, exact-once `replace` и `delete`; проходит schema validation, path traversal/symlink/blocked-path guards и TOCTOU snapshot check.
- Code Agent дополнительно не может менять `.gitattributes`/`.lfsconfig`, чтобы не активировать global Git filters через сгенерированную конфигурацию до approval.
- После agent execution автоматически выполняются Inspect и только safe Verification (`diff_check` / `python_compile`); host `pytest`/frontend build остаются отдельным ручным шагом; основной репозиторий не меняется автоматически.
- Добавлен audit `code_sandbox_agent_runs` с usage/provider cost без сохранения полного исходного prompt/context.
- Alembic head: `20260724_0050`.
- P2-008 targeted verification: 78 tests; TypeScript `--noEmit` проходит.

## v0.11.2 / P2-007.2 — Windows Git config scope hotfix

- Исправлен ложный BLOCK Code Sandbox на Windows, когда Git for Windows/Git LFS задаёт `filter.*` в global/system config пользователя.
- Проверка executable Git filters теперь различает config scopes: запрещаются только `local`/`worktree` filters, которыми управляет сам repository; global/system filters не делают все репозитории непригодными.
- `core.fsmonitor=false` принудительно применяется ко всем sandbox Git-командам, поэтому внешний fsmonitor hook не исполняется независимо от scope настройки.
- Защита от repo-local `clean`/`smudge`/`process` filters сохранена и повторно проверяется перед staging.
- Добавлен regression-тест с глобальными `filter.lfs.clean/smudge/process` и отдельный тест принудительного отключения fsmonitor.
- Unicode-safe Git I/O из P2-007.1 сохранён.
- Alembic head не изменён: `20260724_0049`.
- P2-007.2 verification: 67 tests.

## v0.11.1 / P2-007.1 — Windows Unicode Git I/O hotfix

- Исправлена работа Code Sandbox в Windows-профилях с не-ASCII путями (например, `C:\Users\Глава\...`).
- Git stdout/stderr теперь захватывается как bytes и декодируется с приоритетом UTF-8 вместо неявной ANSI code page Python.
- Добавлен fallback на системную кодировку для локализованных диагностик старых Git for Windows.
- `core.quotepath=false` применяется ко всем sandbox Git-командам; hooks и `diff.external` по-прежнему отключены.
- Проверка Git filters/core.fsmonitor переведена на тот же Unicode-safe runner.
- Добавлены regression-тесты для UTF-8 Windows path output и реального Git-репозитория в каталоге с кириллицей.
- Alembic head не изменён: `20260724_0049`.
- P2-007.1 verification: 65 tests.

## v0.11.0 / P2-007 — Code Sandbox & Verifiable Patches

- Добавлен Workspace-scoped Code Sandbox на detached Git worktree.
- Создание sandbox разрешено только для чистого Git repository и immutable base commit.
- Inspect формирует binary-capable patch, SHA-256 и fingerprint, считает files/insertions/deletions.
- Добавлены уровни риска none/low/medium/high/critical, protected и безусловно blocked paths.
- `.git`, `.venv`, `node_modules`, реальные `.env`, credential/key stores, private keys, SQLite/runtime DB, symlink и submodule entries блокируются без override.
- Protected/high-risk patch требует одноразовый Human Approval с TTL 15 минут; в БД хранится только hash токена.
- Apply повторно вычисляет fingerprint, проверяет clean base и неизменный HEAD, выполняет `git apply --check` и не создаёт commit.
- Git hooks и external diff отключены; repositories с clean/smudge/process filters или executable `core.fsmonitor` отклоняются и повторно проверяются перед staging.
- Verification: `diff_check`, isolated `python_compile`; `pytest` и frontend build disabled-by-default и требуют admin opt-in для trusted repositories.
- Добавлена React-вкладка Code Sandbox с diff preview, risk, verification и approval/apply workflow.
- Добавлена миграция `20260724_0049` с `code_sandbox_sessions` и `code_sandbox_approvals`.
- Alembic CLI приведён к единому `AI_STUDIO_DATABASE_PATH`, чтобы verification/maintenance не уходили в другую SQLite.
- P2-007 targeted regression: 63 tests; TypeScript `--noEmit` проходит.

## v0.10.1 / P2-006.1 — Cancellation Barrier Hotfix

- Исправлена race condition: `cancel_event`, установленный в `reviewer.completed`, больше не позволяет стартовать `council_review_revision`.
- Добавлен cancellation barrier до и после `*.started` для member, auxiliary и synthesis этапов.
- Учитывается как `cancel_event`, так и внешний `cancellation_check`.
- Уже завершённые и оплаченные auxiliary-этапы сохраняются в cancellation ledger; следующий provider-вызов не выполняется.
- Добавлен регрессионный тест отмены на границе `revision.started`.
- Alembic head не меняется: `20260724_0048`.

## v0.10.0 / P2-006 — Advanced Council Orchestration

- Добавлены режимы `Solo`, `Council`, `Best-of-N`, `Review`, `Arbitration` и `Delegate`.
- `Solo` работает с одной моделью без дополнительного synthesis-вызова.
- `Review` выполняет цепочку draft → независимый Reviewer → revision.
- `Arbitration` использует независимого Reviewer и отдельного Arbiter для финального решения.
- `Delegate` создаёт ограниченный план подзадач; максимум 8 субагентов, глубина фиксирована на 1.
- Presets/history/replay сохраняют execution mode, Reviewer, Arbiter и delegation limits.
- P2-005 preflight/ledger учитывает draft, reviewer, planner, delegate и финализирующие стадии.
- Live SSE и React UI показывают стадии advanced orchestration и audit trace.
- Alembic head обновлён до `20260724_0048`.

## v0.9.0 / P2-005 — Actual Cost Ledger, Quotas & Budget-Aware Routing

- Добавлен фактический ledger стоимости по каждому member и synthesis.
- OpenRouter provider-reported cost используется как authoritative источник, когда доступен.
- Добавлен calculated fallback по фактическим токенам и pricing snapshot без подмены неизвестной fallback-стоимости нулём.
- Добавлены 30-минутные cost reservations и reconciliation после сохранения результата.
- Добавлены monthly run/token quotas и per-minute admission limit.
- Добавлены `/api/council/cost/usage` и advisory `/api/council/cost/route`.
- React UI показывает actual/committed/reserved usage и применяет routing recommendation только по действию пользователя.
- Alembic head обновлён до `20260724_0047`.

## v0.8.0 / P2-004 — Council Presets & Cost Control

- Добавлены Workspace-scoped presets: состав, роли, режим, timeout и отдельный председатель.
- Добавлен server-side cost preflight с известной/частичной/неизвестной стоимостью.
- Добавлены monthly budget, soft/hard per-run limits, approval threshold и unknown-cost policy.
- Approval-токены одноразовые, TTL 15 минут и привязаны к fingerprint полного запроса.
- Replay и retry failed проходят тот же cost gate.
- История сохраняет оценку стоимости и идентификатор approval.
- Alembic head: `20260724_0046`.

## v0.7.0 / P2-003 — Live Council

- Добавлены фоновые live-запуски Council и Workspace-изолированный SSE API с
  heartbeat, буфером событий и возобновлением через `Last-Event-ID`.
- Участники по-прежнему выполняются параллельно; UI в реальном времени
  показывает `waiting`, `running`, `success`, `error` и `reused`.
- OpenRouter adapter получил настоящий streaming, который проходит через
  единый AI Gateway с прежними secret leases, нормализацией ошибок, usage и
  событиями.
- Добавлены кооперативная отмена активного запуска и настраиваемый тайм-аут
  каждого участника/председателя.
- Revision `20260724_0045` разрешает долговременный статус `cancelled`; при
  downgrade отменённые записи сохраняются как `failed`.
- Replay переведён на live-поток. Новый retry failed вызывает provider только
  для неуспешных участников и использует сохранённые успешные ответы.
- При отключённом хранении успешных ответов retry failed безопасно возвращает
  конфликт вместо неполного синтеза.
- Добавлены live-экран, поток председателя, выбор тайм-аута, кнопка отмены,
  статус отмены и действие **Повторить ошибки**.
- Терминальное событие доставляется до закрытия SSE даже при конкурентном
  изменении статуса.
- Полный backend-набор содержит 296 тестов; TypeScript/Vite production build
  проходит.

## v0.6.1 / P2-002 — Council Run Persistence & History

- Добавлена миграция `20260716_0044` с таблицами `council_runs` и
  `council_run_members`; актуальная ORM metadata содержит 145 таблиц.
- Завершённые, частичные и полностью неуспешные запуски Council сохраняются
  вместе с составом, ответами, ошибками, token usage и итоговым синтезом.
- Добавлены Workspace-изолированные API списка, карточки, удаления и повторного
  запуска; replay создаёт новую запись со ссылкой на исходную.
- Список поддерживает поиск, фильтры status/mode/date и пагинацию.
- Добавлена Workspace-политика хранения: срок 1–3650 дней, opt-in
  автоматическое удаление, управление сохранением ответов и token usage,
  явная очистка устаревших записей.
- React UI получил раздел истории, фильтры, карточку сохранённого отчёта,
  подтверждения replay/удаления и настройки хранения.
- События истории не содержат вопрос, ответы или итоговый текст.
- Полный backend-набор содержит 286 тестов; TypeScript/Vite production build
  проходит.

## v0.6.0 / P2-001.3 — Workspace compatibility hotfix

- Исправлена несовместимость P2-001.2 с существующим Workspace, политика
  которого ссылается на прежнюю стартовую модель.
- Прежние starter-модели теперь остаются enabled для действующих политик, но
  скрываются из пользовательского каталога через `catalog_hidden`.
- При старте автоматически восстанавливаются только записи, которые были
  отключены P2-001.2 и сохранили точную прежнюю seed-конфигурацию.
- `run_tests.bat` использует отдельную временную SQLite-базу и больше не
  проверяет и не изменяет рабочую `data/ai_studio.db`.
- Добавлены regression-тесты восстановления модели и разрешения Workspace
  context; полный набор содержит 283 теста.
- Alembic head остаётся `20260716_0043`.

## v0.6.0 / P2-001.2 — Council resilience hotfix

- Для временной ошибки конкретной бесплатной модели OpenRouter добавлена одна
  попытка через бесплатный маршрут `openrouter/free`.
- Автоматический fallback не применяется к тарифицируемым моделям и не меняет
  выбранные пользователем настройки.
- Синтезатор устойчиво разбирает JSON в code fence, double-encoded JSON,
  распространённые варианты имён полей и безопасные Python-like словари.
- При частично повреждённом JSON интерфейс извлекает читаемый `final_answer`
  вместо показа сырого объекта.
- В отчёте видны резервный маршрут, код и понятное описание ошибки участника.
- Три прежние неизменённые стартовые модели помечаются как устаревшие без
  удаления; в P2-001.3 их отображение отделено от runtime-совместимости.
- Alembic head остаётся `20260716_0043`.

## v0.6.0 / P2-001.1 — Windows Python 3.13 test hotfix

- Исправлен ложный отказ
  `test_application_can_restart_lifespan_multiple_times`.
- Тест больше не сравнивает `id()` уже освобождённых объектов: Python вправе
  повторно использовать их адреса памяти.
- Предыдущие очереди удерживаются сильными ссылками, а новые экземпляры
  сравниваются оператором `is`.
- Производственный Task Runtime и схема БД не изменялись.

## v0.6.0 — 2026-07-23

### P2-001 — AI Council

- Добавлен реальный многомодельный Council API.
- Реализованы 2–6 независимых участников и пять аналитических ролей.
- Запросы участников выполняются параллельно без блокировки event loop.
- Добавлены структурированный синтез, консенсус, разногласия, рекомендации
  и confidence score.
- При отказе одной модели возвращается частичный результат; ошибка всего
  запуска возникает только при отказе всех участников.
- Добавлена защита синтезатора от prompt injection в ответах участников.
- Добавлен полноценный React-интерфейс Совета и сравнение ответов.
- Добавлены бесплатные модели по умолчанию и явная маркировка тарифицируемых
  моделей.

### P1-020.3.1 — Migration Chain Integrity

- Исправлена baseline-миграция, которая принимала служебную таблицу
  `alembic_version` за существующую схему и пропускала семь базовых таблиц.
- Baseline сделан неизменяемым и независимым от текущей ORM metadata.
- Добавлена безопасная классификация новой, baseline, актуальной и частичной
  unversioned-схемы.
- Проверены полный `upgrade -> downgrade base -> upgrade head` и все 143
  таблицы актуальной metadata.
- `db_upgrade.bat` переведён на безопасный migration manager.

### Поставка

- Обновлены React, TypeScript и Vite с зафиксированными версиями.
- Добавлен `start_studio.bat`.
- Обновлены установка, документация, roadmap и статус проекта.
- Архив поставки очищен от `.env`, баз, логов, `.venv` и `node_modules`.

## v0.5.0 — P1 Core completion

- Завершены P1-013 — P1-020.3: Alembic, lifespan/DI, Task Engine,
  Orchestration, Autonomous Missions, Human Control Center и Secret
  Management.

## v0.1-bootstrap — 2026-07-09

- Создан базовый Python-проект и OpenRouter provider.
- Добавлены консольный запуск, `.env`, логи и Windows-скрипты.
