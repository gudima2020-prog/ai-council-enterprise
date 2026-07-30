# P2-011 — Runtime Policy, Trust and Data Classification

## Статус

**Реализовано и проверено в AI Studio Enterprise v0.15.0.**

P2-011 добавляет единый fail-closed policy layer между Workspace,
AI Gateway, Code Sandbox, Isolated Runtime и экспортом runtime-артефактов.

## Классификация данных

- `public` — данные допустимы для обычных внешних провайдеров;
- `internal` — внутренние рабочие данные со стандартной fail-closed политикой;
- `confidential` — внешняя передача допускается только через явно доверенный
  provider и может требовать Human Approval;
- `restricted` — внешние provider-маршруты и экспорт артефактов запрещены.

Классификация хранится в Workspace policy под ключом
`security.data_classification`.

## Provider trust tiers

- `local` — локальный провайдер в контролируемом контуре;
- `trusted_external` — внешний провайдер, явно разрешённый для доверенных
  сценариев;
- `external` — обычный внешний провайдер;
- `blocked` — провайдер полностью запрещён политикой Workspace.

Trust map хранится под ключом `security.provider_trust`. Для неизвестного
провайдера используется безопасный default `external`.

## Runtime trust tiers

- `safe_host_profile` — только безопасная host-side metadata validation;
- `isolated_container` — выполнение недоверенного кода в Docker boundary;
- `blocked` — runtime запрещён.

Host-side `diff_check` рассматривается как metadata validation. Python,
pytest и frontend build выполняются только через isolated container.

## Policy actions

- `allow`;
- `require_approval`;
- `require_isolation`;
- `deny`.

Каждое решение содержит стабильные reason codes и детерминированный SHA-256
fingerprint. Ошибка разрешения Workspace policy приводит к fail-closed отказу.

## Enforcement

### AI Gateway

- policy оценивается до чтения секрета и до вызова provider adapter;
- проверяется каждый primary и failover route;
- policy rejection прекращает failover и не допускает обход через другой
  провайдер;
- `restricted` разрешает model inference только через `local`;
- `confidential` через `trusted_external` требует approval, а через обычный
  `external` блокируется.

### Code Sandbox и Isolated Runtime

- `diff_check` остаётся безопасной metadata-only host-проверкой;
- исполняемый код направляется в `isolated_container`;
- policy проверяется до Docker image inspection и до создания контейнера;
- runtime decision сохраняется в `metadata_json`;
- reason codes и fingerprint публикуются в audit events.

### Runtime artifacts

- `restricted` export запрещён;
- `confidential` export требует approval и до появления отдельного approval
  workflow блокируется fail-closed;
- `public` и `internal` export разрешены;
- повторная policy-проверка выполняется перед выдачей ZIP через API;
- policy metadata сохраняется при добавлении `artifact_zip_path`, а не
  перезаписывается.

## Workspace Policy UI

Раздел **«Политика»** позволяет:

- выбрать Workspace;
- увидеть effective provider/model/network/filesystem policy;
- задать `public / internal / confidential / restricted`;
- назначить trust tier каждому доступному провайдеру;
- сохранить policy overrides;
- сбросить overrides к effective defaults;
- увидеть предупреждения и фактически применяемые ограничения.

## Совместимость и миграции

- Alembic head остаётся `20260724_0052`;
- новая миграция не требуется: Workspace policy использует существующее
  settings-хранилище, а runtime audit использует существующий `metadata_json`;
- P2-011 сохраняет security boundaries P2-007, P2-008, P2-009 и P2-010.

## Verification

Release verification выполняется командой:

```cmd
verify_p2_011.bat
```

Скрипт проверяет:

- Alembic head;
- policy engine version;
- наличие и подключение Workspace Policy UI;
- полный backend regression suite;
- TypeScript/Vite production build;
- необязательный Docker runtime preflight.

Проверенная Windows-конфигурация:

- backend: **391 passed, 1 skipped**;
- frontend production build: **PASSED**;
- Workspace Policy UI smoke test: **PASSED**;
- пропущенный тест относится к недоступным symlink на текущей Windows-системе
  и не является отказом функциональности.
