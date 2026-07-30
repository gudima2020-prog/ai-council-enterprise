# Version

Текущая версия: **0.15.0 / P2-011**
Статус: **стабильная контрольная точка**
Дата: **2026-07-30**
Alembic head: **`20260724_0052`**

## Основные возможности

### P2-009 — Multi-Provider Model Gateway

- универсальный OpenAI-compatible provider adapter;
- OpenRouter как основной настроенный провайдер;
- SVRTR как дополнительный необязательный провайдер;
- provider health, success rate и EMA latency;
- circuit breaker и безопасный failover;
- intelligent routing по стоимости, задержке, надёжности и качеству;
- поддержка Secret Manager и legacy environment credentials;
- исключение ненастроенных провайдеров из маршрутизации.

### P2-010 — Docker Isolated Runtime

- изолированное выполнение кода в Docker-контейнерах;
- отдельные доверенные runtime-образы Python 3.13 и Node.js 22;
- Docker daemon и runtime image preflight;
- интеграция изолированного runtime с Code Sandbox;
- поддержка Windows через Docker Desktop и WSL 2;
- подготовка runtime-образов через `prepare_p2_010_runtime.bat`.

### P2-011 — Runtime Policy, Trust and Data Classification

- единый fail-closed policy engine с reason codes и SHA-256 fingerprint;
- Workspace data classification: `public`, `internal`, `confidential`,
  `restricted`;
- provider trust tiers: `local`, `trusted_external`, `external`, `blocked`;
- enforcement каждого primary/failover route до adapter и secret injection;
- безопасная host metadata validation и обязательная Docker isolation для
  выполнения кода;
- блокировка restricted artifact export и approval gate для confidential;
- runtime policy snapshots в metadata и audit events;
- Workspace Policy UI для чтения, сохранения и сброса overrides;
- verification-скрипт `verify_p2_011.bat`.

## Проверенная конфигурация

- Alembic head: **`20260724_0052`**;
- backend tests: **391 passed, 1 skipped**;
- frontend production build: **PASSED**;
- Workspace Policy UI smoke test: **PASSED**;
- Docker Desktop / WSL 2 runtime boundary: **READY**;
- P2-011 verification: **PASSED** после выполнения `verify_p2_011.bat`.

Пропущенный тест связан с недоступностью символических ссылок на текущей
Windows-конфигурации и не является отказом функциональности.

## Следующий шаг

Объединить `feature/p2-011` с `main`, повторно выполнить
`verify_p2_011.bat` на merge commit и создать release tag
`v0.15.0-p2-011`.
