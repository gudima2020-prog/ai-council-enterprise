# AI Studio Enterprise — Project Status

Текущий этап: **P3-001 — Documents Workspace**, реализован.
Версия: **0.17.0 / P3-001**.
Alembic head: **`20260806_0057`**.

## Завершённый фундамент P1

- конфигурация, logging, Event Bus и AI Gateway;
- Workspace, проекты, память, модели, настройки и repository layer;
- Task Engine с очередями, retries, зависимостями, approvals, аудитом,
  бюджетами и Dead Letter Queue;
- Orchestration Engine с агентами, tools, planning, review, supervisor,
  observability, distribution и event transport;
- Autonomous Workspace Missions, governance, resources, schedules,
  portfolio, forecasting и learning;
- Human Control Center, authentication, governance, browser security,
  notifications, routing, compliance, retention и evidence;
- Secret Management, policies, leases, runtime injection, внешние providers,
  rotation и health monitoring.

## P1-020.3.1 — стабилизация миграций

Обнаружена и устранена ошибка baseline: при чистой установке служебная таблица
Alembic приводила к пропуску семи core-таблиц. Теперь:

- baseline содержит явный неизменяемый DDL;
- unversioned-база классифицируется до `stamp`;
- частичная схема отклоняется без изменения revision;
- полный round trip миграций проверяется тестом;
- актуальная metadata содержит 165 таблиц.

## P3-001 — Documents Workspace

- safe intake принимает только проверенные PDF, DOCX, XLSX и TXT и не доверяет
  extension/MIME без content validation;
- Workspace-scoped registry использует content-addressed managed storage,
  tombstones, integrity checks и metadata-only evidence;
- deterministic extraction сохраняет exact units/chunks и provenance;
- PDF OCR выполняется только в trusted immutable Docker runtime без сети,
  сохраняет page text/hashes и поддерживает classification retention;
- explicit AI context resolver проверяет Workspace/document/run/source binding,
  persisted hashes, classification, retention и citation provenance;
- summary/question pipeline выполняет model/context preflight, Runtime Policy,
  external-provider acknowledgement и отдельные primary/reviewer approvals;
- primary result скрыт до независимого reviewer approval; rejected/failed runs
  не раскрывают output;
- migration chain завершён revision `20260806_0057`;
- React Documents UI поддерживает registry, upload/delete, extraction/OCR,
  exact preview, explicit source selection, governed analysis и history;
- independent-review closure блокирует stale preview, исключает HTTP caching
  content-bearing responses, показывает exact citation identity, полностью
  раскрывает external reviewer payload и сохраняет failed-run envelopes в UI;
- pre-review Windows P3-001.6 gate: **216 targeted passed** и **647 full
  passed, 1 skipped, 2 warnings**; corrected local P3-001.6a gate:
  **216 targeted passed, 1 warning**, **648 full passed, 2 warnings** и
  frontend production build **PASSED**. Corrected Windows gate остаётся
  обязательным до commit/push.

## P2-012 — Policy Approval Workflow & Decision Evidence

- независимый approval domain связывает решение с Workspace, operation,
  policy version/fingerprint и точным subject scope;
- состояния `pending`, `approved`, `denied`, `expired`, `revoked` и `consumed`
  работают fail-closed с ограниченным TTL;
- raw token возвращается только один раз при approve, а в БД хранится только
  domain-separated SHA-256 hash;
- миграция `20260731_0053` добавляет Workspace-isolated approvals и глобальную
  append-only SHA-256 evidence chain;
- REST API использует Human Control permissions, authenticated actor и
  Workspace binding;
- AI Gateway создаёт exact route approval и atomically consumes token перед
  non-streaming или streaming provider call;
- каждый failover candidate оценивается отдельно и не может использовать
  approval другого provider/model route;
- runtime artifact approval связан с runtime run, sandbox session, ZIP SHA-256
  и manifest fingerprint;
- ZIP выдаётся только после fresh policy evaluation, atomic consume и
  повторной проверки descriptor;
- React Approval Center поддерживает очередь, фильтры, exact scope preview,
  reason codes, TTL, approve/deny/revoke и evidence;
- frontend production build: **PASSED**;
- backend regression: **454 passed, 1 skipped, 2 warnings**;
- Alembic `heads` и `current`: **`20260731_0053 (head)`**.

## P2-011 — Runtime Policy, Trust and Data Classification

- единый fail-closed policy engine выдаёт `allow`, `require_approval`,
  `require_isolation` или `deny`;
- каждое решение содержит reason codes и детерминированный SHA-256 fingerprint;
- Workspace хранит data classification и provider trust map;
- AI Gateway проверяет каждый primary/failover route до adapter и secret lease;
- host разрешён только для metadata-only `diff_check`, исполняемый код проходит
  Docker isolation;
- restricted artifact export запрещён, confidential требует approval;
- policy snapshot сохраняется в runtime metadata и audit events;
- React UI позволяет управлять классификацией и trust tiers;
- полный regression suite и frontend production build проходят;
- Alembic head не изменился: `20260724_0052`.

## P2-008 — Council-to-Sandbox Agent Execution

- сохранённый Council run можно передать в Code Sandbox как implementation decision;
- capability-limited Code Agent получает только явный task, выбранный text context и exact writable path allowlist;
- shell, network, secrets, произвольный filesystem и automatic apply отсутствуют;
- structured JSON edits проходят строгую schema/path/size validation и TOCTOU snapshot check;
- P2-007 blocked paths недоступны агенту ещё до LLM-вызова;
- изменения выполняются только в detached worktree, затем автоматически проходят Inspect и опциональный Verification;
- `code_sandbox_agent_runs` сохраняет аудит, usage и provider-reported cost без полного prompt/source context;
- миграция `20260724_0050`; targeted verification включает agent execution tests;
- точечная `replace`-операция требует exact-once match и уменьшает output token overhead;
- `.gitattributes` и `.lfsconfig` дополнительно запрещены именно Code Agent, чтобы модель не могла активировать trusted-global Git filters через новый attributes mapping до approval.

## P2-007.2 — Windows Git config scope hardening

- global/system Git filters пользователя (включая Git LFS) больше не вызывают ложный отказ для чистого repository;
- local/worktree executable filters самого repository остаются запрещены;
- `core.fsmonitor` принудительно отключён во всех Git subprocess Code Sandbox;
- Unicode-safe Windows path handling из P2-007.1 сохранён;
- targeted verification: 67 tests.

## P2-001 — AI Council

- `POST /api/council/run` и `GET /api/council/status`;
- 2–6 уникальных provider/model участников;
- параллельный запуск через общий AI Gateway;
- роли analyst, critic, strategist, researcher и risk;
- structured synthesis и безопасный fallback;
- partial result при локальном отказе модели;
- события запуска и участников без сохранения текста ответа в event payload;
- React UI для выбора состава и просмотра итогов;
- бесплатный стартовый состав и маркировка платных маршрутов.

## P2-001.2 — стабилизация реального OpenRouter

- `RATE_LIMIT`, timeout, network и временная недоступность конкретной free
  модели допускают одну попытку через `openrouter/free`;
- тарифицируемые модели автоматически не заменяются;
- неуспешный fallback сохраняет частичный результат;
- structured synthesis принимает распространённые варианты JSON и не
  показывает пользователю сырой JSON при повреждении остальных полей;
- UI показывает исходную модель, резервный маршрут и код ошибки;
- legacy defaults помечаются как скрытые только при совпадении прежней
  seed-конфигурации; записи не удаляются.

## P2-001.3 — совместимость Workspace и тестовой среды

- существующая Workspace policy может продолжить использовать прежнюю
  starter-модель;
- записи, отключённые точным механизмом P2-001.2, восстанавливаются при старте;
- скрытие устаревшей модели в React-каталоге не меняет её runtime availability;
- изменённые и пользовательские записи не перезаписываются;
- `run_tests.bat` направляет приложение в отдельную временную SQLite-базу и
  удаляет её после тестов.

## P2-002 — история запусков Council

- миграция `20260716_0044` добавляет `council_runs` и
  `council_run_members`;
- завершённые, частичные и полностью неуспешные запуски сохраняются вместе с
  составом, длительностью, ошибками и синтезом;
- API поддерживает список с фильтрами, карточку, replay и удаление;
- повтор создаёт новый запуск и сохраняет ссылку `replay_of_run_id`;
- все операции истории строго ограничены текущим Workspace;
- React UI содержит историю, фильтры, пагинацию, просмотр отчёта и
  подтверждения перед replay/удалением;
- политика хранения позволяет отдельно отключить сохранение ответов участников
  и token usage, настроить срок и включить автоматическую очистку;
- явная очистка удаляет только записи текущего Workspace старше настроенного
  срока.

## P2-003 — Live Council

- live-запуск отделён от HTTP-запроса и продолжает работу в фоновой задаче;
- `GET /api/council/live/{run_id}/events` передаёт именованные SSE-события,
  хранит ограниченный буфер и поддерживает возобновление через
  `Last-Event-ID`;
- участники запускаются параллельно, а UI показывает состояния каждого из них;
- OpenRouter adapter передаёт реальные provider chunks через единый AI Gateway;
- активный запуск можно отменить; provider stream закрывается кооперативно;
- тайм-аут участника настраивается в диапазоне 5–600 секунд и сохраняется для
  replay;
- отменённые запуски сохраняются со статусом `cancelled`;
- replay переведён на live-поток;
- retry failed повторяет только неуспешные модели и использует сохранённые
  успешные ответы; если политика хранения удалила их текст, операция безопасно
  отклоняется;
- live-status, stream, cancellation, replay и retry строго изолированы по
  Workspace;
- завершённые live-состояния хранятся в памяти ограниченное время, а
  долговременный отчёт остаётся в SQLite.

## Проверка релиза

- backend regression suite;
- отдельные round-trip тесты Alembic;
- Council unit/API tests;
- отдельные тесты free fallback, запрета retry для metered model, parser
  recovery и безопасного скрытия legacy defaults;
- regression-тесты восстановления P2-001.2 model record и разрешения
  существующего Workspace context;
- regression test lifespan использует identity при удерживаемых strong refs и
  не зависит от повторного использования `id()` в Python 3.13;
- тесты repository/service/API истории, Workspace isolation, replay, retention
  и round-trip миграции;
- тесты live manager, SSE API, переподключения, atomic terminal delivery,
  cancellation, timeout, retry failed и OpenRouter chunks;
- P2-004 targeted tests: presets, known/unknown pricing, fingerprint-bound one-time approval;
- P2-005 targeted tests: provider/calculated actual cost, ledger settlement, reservations, quotas и budget-aware routing;
- P2-006 targeted tests: Solo, Best-of-N, Review, Arbitration, bounded Delegate, independence validation и advanced cost ledger;
- P2-007 targeted tests: реальные Git worktrees, fingerprint/TOCTOU, protected/blocked paths, approvals, safe apply, allowed roots, Git execution guards и Alembic CLI DB override;
- P2-008 targeted tests: exact writable allowlist, Council handoff, malformed agent output, blocked secret paths, `.gitattributes` guard, exact-once replace, protected-file approval flow, запрет auto host-test/build, no-auto-apply и concurrent-edit TOCTOU;
- P2-012 targeted regression: domain, persistence, REST API, Gateway coordinator/enforcement/API, runtime artifact coordinator/enforcement;
- P3-001 targeted regression: intake, registry/storage/API, extraction,
  OCR, context/citations, Document AI persistence/service/API, Gateway
  classification/policy/approvals и migration manager;
- frontend production build и TypeScript compilation: **PASSED** локально на
  corrected P3-001.6a tree;
- corrected local full backend regression: **648 passed, 2 warnings**;
- Windows release gate до review closure проходил с **647 passed, 1 skipped,
  2 warnings** и должен быть повторён после применения исправлений.

## P2-004 — Council Presets & Cost Control

- presets сохраняют роли, режим, timeout и отдельного председателя;
- preflight считает ожидаемые input/output tokens и стоимость до запуска;
- free-модели имеют известную нулевую стоимость, metered-модели без прайса остаются `unknown`;
- Workspace policy задаёт monthly budget, soft/hard per-run limits и approval threshold;
- unknown-cost policy: allow / require approval / block;
- approval token одноразовый, имеет TTL 15 минут и привязан к fingerprint запроса;
- replay/retry failed используют тот же cost gate;
- миграция `20260724_0046` добавляет presets, approvals и поля cost audit в `council_runs`.

## P2-005 — Actual Cost Ledger & Budget-Aware Routing

- `council_cost_ledger` сохраняет отдельные cost-lines участников и итогового синтеза;
- provider-reported cost имеет приоритет, локальный pricing применяется только при достоверном token usage;
- `council_cost_reservations` резервирует ожидаемые стоимость и токены до завершения запуска;
- завершённые reservations reconciled с фактическим run и usage;
- Workspace policy получила monthly run/token quotas и per-minute admission limit;
- usage API различает actual, committed и reserved spend;
- budget-aware router работает в advisory mode, сохраняет provider и уникальность состава;
- React UI показывает actual ledger/quotas и позволяет вручную применить рекомендованный состав;
- миграция `20260724_0047` добавляет ledger, reservations и actual-cost audit fields.

## P2-006 — Advanced Council Orchestration

- шесть execution modes: Solo / Council / Best-of-N / Review / Arbitration / Delegate;
- Review использует независимого Reviewer, Arbitration — отдельного Reviewer и Arbiter;
- Delegate ограничивает число субагентов значением 1–8 и жёстко фиксирует глубину 1;
- orchestration trace сохраняет auxiliary calls, finalizer stage и фактическое делегирование;
- presets/history/replay сохраняют advanced orchestration configuration;
- preflight/reservation/actual ledger учитывают все дополнительные стадии;
- миграция `20260724_0048` добавляет execution mode и orchestration preset fields.

## P2-007 — Code Sandbox & Verifiable Patches

- detached Git worktree отделяет рабочие изменения от базового working tree;
- inspect создаёт binary-capable patch, SHA-256 и fingerprint, считает churn и классифицирует риск;
- реальные `.env`, key/credential stores, runtime DB, `.git`, `.venv`, `node_modules`, symlink/submodule блокируются без override;
- `.env.example`, dependency manifests, migrations, CI, database/security/control файлы относятся к protected и требуют Human Approval;
- Git hooks/external diff отключены, clean/smudge/process filters и executable fsmonitor отклоняются;
- verification включает safe `diff_check`, isolated `python_compile`; host `pytest`/frontend build требуют явного admin opt-in для доверенного repo;
- apply повторно проверяет fingerprint, clean base, исходный HEAD и `git apply --check`; commit автоматически не создаётся;
- approval одноразовый и привязан к точному fingerprint;
- миграция `20260724_0049` добавляет sandbox sessions и approvals;
- worktree не позиционируется как OS security sandbox — недоверенное исполнение остаётся задачей container/VM runtime.

## Следующий этап

**P3-002 — Governed Developer Agent Profiles**: progressive-disclosure
repository policies, tool execution hooks, independent Standards/Spec review,
context checkpoints и signed/pinned skills registry. После него запланирован
**P3-003 — Governed Local Utilities Workspace**.
