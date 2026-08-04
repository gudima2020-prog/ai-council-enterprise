# Version

Текущая версия: **0.16.0 / P2-012**
Статус: **стабильная контрольная точка**
Дата: **2026-08-04**
Alembic head: **`20260731_0053`**

## Основные возможности

### P2-011 — Runtime Policy, Trust and Data Classification

- единый fail-closed policy engine с reason codes и SHA-256 fingerprint;
- Workspace data classification и provider trust tiers;
- enforcement каждого Gateway primary/failover route;
- обязательная Docker isolation для исполнения недоверенного кода;
- artifact export policy и runtime audit snapshots;
- Workspace Policy UI.

### P2-012 — Policy Approval Workflow & Decision Evidence

- Workspace-scoped approval domain для точного Runtime Policy decision;
- состояния `pending`, `approved`, `denied`, `expired`, `revoked`, `consumed`;
- TTL 60 секунд — 24 часа, default 15 минут;
- raw capability token возвращается только один раз при approve;
- хранение только domain-separated SHA-256 token hash;
- exact scope fingerprint для provider/model/request или runtime artifact;
- migration `20260731_0053`;
- Workspace-isolated repository/service и append-only SHA-256 evidence chain;
- Human Control-bound REST API для request/list/get/approve/deny/revoke/evidence;
- AI Gateway approval coordinator и atomic consume перед provider call;
- независимая проверка каждого failover candidate;
- streaming не выдаёт delta до успешного consume;
- approval-aware Workspace Gateway API;
- runtime artifact binding к run, ZIP SHA-256 и manifest fingerprint;
- fresh policy evaluation и повторная descriptor check перед выдачей bytes;
- React Approval Center с queue, filters, exact scope, TTL и decision evidence;
- verification-скрипт `verify_p2_012.bat`.

## Проверенная конфигурация

- Alembic heads: **`20260731_0053 (head)`**;
- Alembic current: **`20260731_0053 (head)`**;
- backend tests: **454 passed, 1 skipped, 2 warnings**;
- frontend TypeScript/Vite production build: **PASSED**;
- targeted Policy Approval regression: **PASSED**;
- local/remote feature branch synchronization: **PASSED**.

Пропущенный тест связан с недоступностью символических ссылок на текущей
Windows-конфигурации и не является отказом функциональности.

Известные неблокирующие предупреждения:

- Starlette TestClient использует deprecated integration с `httpx`;
- example plugin создаёт duplicate OpenAPI Operation ID.

## Release procedure

1. выполнить `verify_p2_012.bat` на release closure commit;
2. отправить `feature/p2-012`;
3. объединить ветку с `main`;
4. повторно выполнить `verify_p2_012.bat` на merge commit;
5. создать annotated tag `v0.16.0-p2-012`;
6. отправить `main` и tag в `origin`.
