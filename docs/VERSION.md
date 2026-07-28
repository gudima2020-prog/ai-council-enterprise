# Version

Текущая версия: **0.14.0 / P2-010**
Статус: **стабильная контрольная точка**
Дата: **2026-07-24**
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
- verification-скрипт `verify_p2_010.bat`;
- подготовка runtime-образов через `prepare_p2_010_runtime.bat`.

## Проверенная конфигурация

- Docker Desktop: **READY**;
- WSL 2 backend: **READY**;
- Python runtime image: `ai-studio-runtime-python:py313-v1`;
- Node runtime image: `ai-studio-runtime-node:node22-v1`;
- backend tests: **98 passed, 1 skipped**;
- frontend production build: **PASSED**;
- P2-010 verification: **PASSED**.

Пропущенный тест связан с недоступностью символических ссылок на текущей Windows-конфигурации и не является отказом функциональности.

## Следующий этап

**P2-011 — Runtime Policy, Trust and Data Classification**.
