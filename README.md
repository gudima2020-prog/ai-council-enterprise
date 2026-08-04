# AI Studio Enterprise

Локальная enterprise-платформа для работы с ИИ. **AI Council** — первый
пользовательский модуль: несколько моделей независимо анализируют вопрос,
после чего председатель формирует общий вывод, консенсус, разногласия,
рекомендации и оценку уверенности.

Текущая версия: **v0.16.0 / P2-012**.
Alembic head: `20260731_0053`.

## Что уже работает

- FastAPI backend и React + TypeScript frontend;
- универсальный чат через Multi-Provider Model Gateway;
- OpenRouter + SVRTR/SaveRouter через универсальный OpenAI-compatible adapter;
- provider health, persisted reliability/latency, circuit breaker и явный failover;
- intelligent routing по quality/cost/latency/reliability;
- fail-closed Runtime Policy для каждого primary/failover route до adapter и secret injection;
- Workspace-scoped Policy Approval Workflow с exact subject scope, TTL и одноразовым capability token;
- approval-aware Gateway inference без failover bypass и с atomic consume перед provider call;
- approval-bound runtime artifact export по ZIP SHA-256 и manifest fingerprint;
- append-only decision evidence и отдельный React-раздел «Approvals»;
- Workspace data classification и provider trust tiers с отдельным разделом «Политика»;
- AI Council с режимами Solo / Council / Best-of-N / Review / Arbitration / Delegate;
- Solo использует ровно одну модель; остальные коллективные режимы — 2–6 участников;
- независимые Reviewer и Arbiter для проверяемого review/arbitration;
- bounded delegation: PRIMARY создаёт до 8 субагентов, глубина жёстко ограничена 1;
- параллельные ответы ролей: аналитик, критик, стратег, исследователь,
  риск-аналитик;
- устойчивый итоговый синтез, безопасный free fallback и сохранение частичных
  результатов;
- история завершённых, частичных и неуспешных запусков с фильтрами;
- просмотр сохранённого отчёта, повтор с тем же составом и безопасное удаление;
- живой SSE-прогресс участников и потоковый итог председателя;
- отмена активного запуска и настраиваемый тайм-аут каждого участника;
- повтор только неуспешных участников с использованием сохранённых успешных
  ответов;
- отдельная для каждого Workspace политика хранения ответов и token usage;
- Actual Cost Ledger по участникам и председателю, включая provider-reported cost и безопасный локальный расчёт;
- cost reservations, месячные квоты запусков/токенов и admission rate limit;
- budget-aware routing с ручным применением более экономичного состава;
- Code Sandbox на detached Git worktree: основной working tree не меняется во время работы агента;
- SHA-256 patch fingerprint, risk classification, protected/blocked paths и 10 MiB patch cap;
- Git config scope hardening: repo-local/worktree executable filters запрещены, global Git LFS не вызывает ложный BLOCK, `core.fsmonitor` принудительно отключён;
- verification-профили `diff_check`, изолированный `python_compile`, `pytest` и frontend build;
- runtime policy snapshots, fail-closed artifact export controls и повторная проверка перед выдачей ZIP;
- одноразовый Human Approval для protected/high-risk патчей и safe apply без автоматического commit;
- совместимость существующих Workspace policy с прежними starter-моделями;
- Workspace, проекты, память, настройки и каталог моделей;
- Task Engine, Orchestration Engine и Autonomous Missions;
- Human Control Center, аудит, бюджеты и approval gates;
- управление секретами, краткоживущие leases и внешние Secret Providers;
- безопасная миграция существующей SQLite-базы через Alembic;
- Windows-скрипты установки, миграции, запуска и тестирования.

## Требования

- Windows 10/11;
- Python 3.11+ (рекомендуется 3.12);
- Node.js `20.19+` или `22.12+`;
- ключ хотя бы одного поддерживаемого LLM-провайдера (OpenRouter и/или SVRTR).

## Быстрый запуск на Windows

Распакуйте архив в отдельную папку. Для обновления существующей установки
сначала сделайте резервную копию, затем распакуйте новые файлы поверх проекта:
архив не содержит `.env`, базы и runtime-данные, поэтому они не перезаписываются.

```cmd
install.ba
notepad .env
db_upgrade.ba
run_tests.ba
start_studio.ba


В `.env` укажите:

```env
OPENROUTER_API_KEY=<YOUR_OPENROUTER_API_KEY>
OPENROUTER_DEFAULT_MODEL=openrouter/free
# либо SVRTR_API_KEY=<YOUR_SVRTR_API_KEY>


После запуска откройте:

- UI: <http://127.0.0.1:5173>
- API docs: <http://127.0.0.1:8000/docs>
- health: <http://127.0.0.1:8000/api/health>

Если удобнее запускать процессы вручную, откройте два терминала:

```cmd
run_api.ba


```cmd
run_frontend.ba


## AI Council

Во вкладке **AI Council**:

1. выберите от 2 до 6 моделей;
2. выберите режим;
3. сформулируйте вопрос;
4. выберите тайм-аут одного участника;
5. нажмите **Запустить Совет** и наблюдайте живой прогресс;
6. при необходимости нажмите **Отменить**;
7. следите за блоком **Actual Cost Ledger**: он показывает факт, reservations и квоты;
8. при необходимости нажмите **Подобрать экономичный состав** — рекомендация применяется только после вашего действия;
9. откройте **История**, чтобы найти, просмотреть, повторить, повторить только
   ошибки или удалить сохранённый запуск.

По умолчанию выбираются три бесплатных модели/маршрута из проверенного
каталога. Модели с тарификацией явно помечены. Один запуск выполняет
обычно `N + 1` API-запросов: `N` участников и один итоговый синтез. При
временной ошибке конкретной бесплатной модели выполняется одна дополнительная
попытка через `openrouter/free`; платная модель автоматически не подставляется.

API:

```tex
GET  /api/council/status
POST /api/council/run
POST /api/council/live
GET  /api/council/live/{run_id}
GET  /api/council/live/{run_id}/events
DELETE /api/council/live/{run_id}
GET  /api/council/runs
GET  /api/council/runs/{run_id}
POST /api/council/runs/{run_id}/replay
POST /api/council/runs/{run_id}/replay/live
POST /api/council/runs/{run_id}/retry-failed
DELETE /api/council/runs/{run_id}
GET  /api/council/retention
PUT  /api/council/retention
POST /api/council/retention/purge


История строго ограничена текущим `X-Workspace-ID`. Политика хранения по
умолчанию сохраняет ответы участников и token usage 365 дней, но автоматическое
удаление выключено до явного включения пользователем.

## Code Sandbox

Во вкладке **Code Sandbox** укажите путь к чистому Git-репозиторию и создайте
detached worktree. После того как агент или разработчик изменит файлы только в
worktree, выполните **Inspect patch**, затем verification. Protected/high-risk
изменения требуют отдельного **Human Approval**. Перед применением система
повторно сверяет fingerprint, чистоту базы и исходный `HEAD`; commit не создаётся.

По умолчанию разрешён только текущий репозиторий AI Studio. Для других доверенных
репозиториев задайте `AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS`. `pytest` и
`frontend_build` исполняют код проекта и поэтому отключены до явного
`AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION=1`. Git worktree не является
контейнером или VM.

Подробности: [P2-007 Code Sandbox](docs/P2_007_CODE_SANDBOX.md).

P2-008 добавляет capability-limited Code Agent: handoff из Council, явный allowlist файлов, структурированные edits без shell/network/secrets, автоматический Inspect/Verification и отдельный Human Apply. Подробности: [P2-008 Council-to-Sandbox Agent Execution](docs/P2_008_COUNCIL_TO_SANDBOX_AGENT_EXECUTION.md).

## Миграции и сохранность данных

Используйте `db_upgrade.bat`, а не прямой `alembic stamp`. Менеджер миграций
различает новую базу, полную P1-013 baseline-схему и полную актуальную схему.
Частичная или неоднозначная схема останавливается до изменения revision.

Проверить состояние:

```cmd
db_current.ba
db_history.ba


## Проверка разработки

```cmd
run_tests.ba


Скрипт создаёт отдельную временную SQLite-базу, удаляет её после прогона и не
изменяет рабочую `data/ai_studio.db`.

Frontend production build:

```cmd
cd frontend
npm run build


## Безопасность

Не публикуйте `.env`, API-ключи, каталоги `data/`, `logs/`, `diagnostics/`,
`.venv/` и `frontend/node_modules/`. Эти пути исключены из поставляемого
архива и `.gitignore`.

Подробности:

- [статус проекта](docs/PROJECT_STATUS.md);
- [AI Council API и UI](docs/P2_001_AI_COUNCIL_API_UI.md);
- [история и хранение Council](docs/P2_002_COUNCIL_HISTORY.md);
- [Live Council](docs/P2_003_LIVE_COUNCIL.md);
- [Code Sandbox & Verifiable Patches](docs/P2_007_CODE_SANDBOX.md);
- [Policy Approval Workflow](docs/P2_012_POLICY_APPROVAL_WORKFLOW.md);
- [безопасные миграции](docs/P1_013_ALEMBIC_DATABASE_MIGRATIONS.md);
- [установка на Windows](docs/SETUP_WINDOWS.md).


## P2-009 Multi-Provider Model Gateway

Провайдеры не являются взаимозаменяемыми скрыто. Автоматический failover используется
только для явно заданных маршрутов `AI_STUDIO_GATEWAY_FAILOVER_JSON` или через
`AI_STUDIO_DEFAULT_PROVIDER=auto`. Circuit breaker учитывает реальные последовательные
ошибки, а streaming может переключить провайдера только до первого отправленного токена.

SVRTR подключается через OpenAI-compatible endpoint `https://api.svrtr.org/v1`.
Рекомендуемый credential mode — Secret Manager reference `AI_STUDIO_SVRTR_SECRET_REF`;
`SVRTR_API_KEY` оставлен как локальный legacy fallback.

API:

```tex
GET  /api/gateway/status
GET  /api/gateway/providers
POST /api/gateway/route
POST /api/gateway/tes



## P2-010 Docker Isolated Runtime

Недоверенные pytest/frontend build могут выполняться в disposable Docker runtime без сети, с read-only source mount и лимитами CPU/RAM/PID/time. Подготовка локальных runtime images выполняется явно через `prepare_p2_010_runtime.bat`.

## P2-012 Policy Approval Workflow & Decision Evidence

Sensitive Runtime Policy decisions can create a Workspace-scoped approval
request bound to the exact operation subject and policy fingerprint. Approval
tokens are returned once, stored only as domain-separated hashes and consumed
atomically before the external inference or artifact byte release.

The **Approvals** UI shows the pending queue, exact scope, reason codes, TTL,
approve/deny/revoke actions and append-only evidence. Gateway approvals cannot
authorize another provider/model route, and artifact approvals cannot authorize
a changed ZIP, manifest, runtime run or Workspace.
