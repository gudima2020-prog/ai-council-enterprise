# AI Studio Enterprise — Roadmap

Версия плана: **0.17.0**
Дата обновления: **2026-08-07**

## Phase 1 — Enterprise Core

Статус: **завершён до P1-020.3**.

- Core, Workspace, repositories, memory и model catalog;
- Task Engine и Orchestration Engine;
- Autonomous Missions;
- Human Control Center;
- Secret Management и внешние Secret Providers;
- Alembic head `20260806_0057`.

## Phase 2 — Product Modules

### P2-001 — AI Council

Статус: **реализован в v0.6.0**.

- multi-model execution;
- structured consensus;
- confidence score;
- comparison UI;
- partial failure handling;
- one-shot free-model fallback и resilient synthesis parser.

### P2-002 — Council History

Статус: **реализован в v0.6.1**.

- persistence завершённых, частичных и неуспешных запусков;
- изолированная Workspace history с фильтрами;
- просмотр сохранённого отчёта, replay и удаление;
- retention policy для текстов, token usage и очистки.

### P2-003 — Live Council

Статус: **реализован в v0.7.0**.

- SSE streaming с возобновлением по `Last-Event-ID`;
- живой progress участников и потоковый синтез;
- cancellation и настраиваемый timeout участника;
- retry только неуспешных участников с повторным использованием сохранённых
  успешных ответов.

### P2-004 — Presets and Cost Control

Статус: **реализован в v0.8.0**.

- reusable Council presets с явными ролями;
- отдельная модель-председатель;
- server-side cost preflight;
- Workspace budgets, soft/hard limits и approval gates;
- one-time fingerprint-bound approvals для дорогой/неизвестной стоимости.

### P2-005 — Actual Cost Ledger & Budget-Aware Routing

Статус: **реализован в v0.9.0**.

- фактический cost ledger по каждому member/synthesis;
- provider-reported cost с безопасным calculated fallback;
- reservations для защиты бюджета от параллельного oversubscription;
- monthly run/token quotas и admission rate limit;
- Workspace usage API: actual / committed / reserved;
- advisory budget-aware routing без скрытой замены моделей;
- UI фактических расходов и ручного применения экономичного состава.

### P2-006 — Advanced Council Orchestration

Статус: **реализован в v0.10.0**.

- Solo / Council / Best-of-N / Review / Arbitration / Delegate;
- независимый Reviewer и отдельный Arbiter;
- bounded delegation: максимум 8 субагентов, глубина строго 1;
- orchestration trace в истории и Live SSE;
- общие P2-005 budget/preflight/ledger для всех дополнительных стадий.

### P2-007 — Code Sandbox

- Git worktree isolation для параллельных агентов;
- проверяемые patch-наборы;
- protected paths и Human Approval перед применением;
- CI/test evidence перед merge/apply.

### P2-008 — Council-to-Sandbox Agent Execution

Статус: **реализован в v0.12.0**.

- handoff final Council decision в Code Sandbox;
- capability-limited coding agent без shell/network/secrets;
- explicit context и exact writable-path allowlist;
- structured full-file edits с schema/path/size/TOCTOU validation;
- автоматические Inspect/Verification, но никогда automatic Apply;
- audit usage/cost и operation metadata.

### P2-009 — Multi-Provider Model Gateway

Статус: **реализован в v0.13.0**.

- универсальный OpenAI-compatible provider adapter;
- SVRTR/SaveRouter как дополнительный, но не единственный provider;
- provider health checks, failover и circuit-breaker;
- capability registry (text/vision/tools/context limits);
- routing по качеству, стоимости, latency и reliability;
- Workspace trust policy для чувствительных данных.


### P2-010 — Isolated Runtime / Container Execution Boundary

Статус: **реализован в v0.14.0**.

- Docker process/CPU/RAM/PID/time limits для недоверенного выполнения;
- `network=none`, read-only root/source и capability drop;
- disposable tmpfs execution environment для pytest/build;
- evidence/artifact collection без writable host mount.


### P2-011 — Runtime Policy, Trust and Data Classification

Статус: **реализован в v0.15.0**.

- Workspace data classification и provider trust tiers;
- deterministic fail-closed policy engine с reason codes/fingerprint;
- enforcement каждого AI Gateway primary/failover route;
- host metadata validation и обязательный isolated runtime для кода;
- artifact export controls и runtime policy audit snapshots;
- Workspace Policy UI и release verification.


### P2-012 — Policy Approval Workflow & Decision Evidence

Статус: **реализован в v0.16.0**.

- Workspace-scoped approval domain с exact subject и policy fingerprints;
- pending/approved/denied/expired/revoked/consumed state machine;
- one-time domain-hashed capability tokens и atomic database consume;
- append-only SHA-256 decision evidence;
- Human Control-bound REST API и Approval Center UI;
- AI Gateway enforcement без failover bypass;
- runtime artifact enforcement по ZIP SHA-256 и manifest fingerprint;
- release verification: 454 backend tests passed, frontend build passed.

## Phase 3 — Additional Workspaces

### P3-001 — Documents Workspace

Статус: **реализован в v0.17.0**.

- P3-001.1: safe intake core для PDF, DOCX, XLSX и TXT;
- P3-001.2a: Workspace registry и content-addressed managed storage;
- P3-001.2b: Human Control-bound multipart REST API;
- P3-001.3a: deterministic PDF/DOCX/XLSX/TXT extraction core;
- P3-001.3b: persisted extraction runs, units, chunks and Workspace API;
- P3-001.4a: isolated PDF OCR core with trusted Docker runtime;
- P3-001.4b: Workspace OCR persistence, retention, retry and bounded API;
- P3-001.5a: explicit local AI context, conservative token budget,
  prompt-injection warnings и exact citation core;
- P3-001.5b: governed summary/question Gateway pipeline, persisted evidence,
  exact citations, independent reviewer, one-time approvals и retention;
- P3-001.6: Workspace registry UI, upload/delete, extraction/OCR status,
  exact preview/provenance, explicit source selection, governed analysis
  workflow, history и release verification;
- P3-001.6a: independent-review closure — stale preview guards, no-store
  content boundary, exact result citations, complete external-reviewer
  disclosure, token cleanup и persisted failed-run UI handling.

### P3-002 — Governed Developer Agent Profiles

Статус: **в разработке; P3-002.1a, P3-002.1b и P3-002.2a и P3-002.2b-A реализованы**.

- progressive-disclosure repository policy и context checkpoints;
- Agent Policy Profiles поверх Workspace Policy и Human Control;
- P3-002.1a: immutable manifests, exact tool-capability bindings, шесть audited
  built-in profiles, deterministic allow/require-approval/deny evaluation и
  content-free checkpoint с integrity fingerprint;
- P3-002.1b: Workspace-scoped immutable custom profile versions, trusted
  tool-capability catalog, exact fingerprint-bound active selection, Alembic
  `20260807_0058` и Human Control-bound REST API;
- P3-002.2a: pure fail-closed Before Tool Execution composition core для
- P3-002.2b-A: trusted Tool Registry / Workspace Policy / Runtime Policy adapters and governed ToolDefinition binding — implemented; runtime wiring deferred.
  Agent Profile + Tool Registry + Workspace Policy + Runtime Policy +
  trusted Human Control evidence, включая сохранение require-isolation;
- runtime wiring и After Tool Execution enforcement hooks;
- независимые Standards/Spec reviewer и diff-simplification gates;
- signed, pinned и license-aware registry skills/plugins без auto-update.

### P3-003 — Governed Local Utilities Workspace

Статус: **запланировано после P3-002**.

- enforceable Local Tool Registry и capability manifests;
- audited PDF/document transforms без внешней передачи данных;
- token/context/RAG inspectors и prompt-injection warning scanner;
- response comparator/eval scorecard;
- автоматический no-network privacy gate и Local Utilities UI.

Архитектурные границы P3-002/P3-003 описаны в
`docs/GOVERNED_AGENT_LOCAL_UTILITIES.md`.

Остальные направления:

- Research: sources, citations, reports;
- Browser: controlled sessions and evidence;
- Code: repository-aware assistance;
- Crypto AI: market data, strategies and risk.

## Phase 4 — Desktop and Release 1.0

- Tauri desktop shell;
- installer and signed builds;
- backup/restore and update channel;
- end-to-end security review;
- operator and user documentation.

## Release 1.0 criteria

- stable Core and migration policy;
- Chat and AI Council with persistence;
- Memory v1;
- at least one document/research workflow;
- authenticated local UI;
- reproducible Windows packaging.
