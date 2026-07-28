# P2-005 — Actual Cost Ledger, Quotas & Budget-Aware Routing

Версия: **v0.9.0**
Alembic revision: **`20260724_0047`**

## Цель

P2-004 умел оценивать стоимость до запуска. P2-005 замыкает финансовый контур:
предварительная оценка превращается в reservation, а завершившийся запуск — в
фактические строки ledger. Квоты и бюджет учитывают уже выполняющиеся запуски,
поэтому параллельные запросы не могут независимо израсходовать один и тот же
остаток лимита.

## Actual Cost Ledger

Для каждого участника и итогового синтеза создаётся отдельная строка
`council_cost_ledger` с:

- requested/resolved model;
- provider;
- input/output/total tokens;
- provider-reported cost, если он доступен;
- фактической стоимостью;
- источником стоимости: `provider`, `calculated`, `free`, `unknown`;
- pricing snapshot, использованным для локального расчёта.

Приоритет источников:

1. стоимость, которую вернул provider;
2. бесплатный тариф с подтверждённым `billing=free`;
3. расчёт по фактическим токенам и известной цене каталога;
4. `unknown`, если достоверный расчёт невозможен.

Для fallback-цепочки локальный пересчёт не подменяет неизвестную стоимость:
если provider не сообщил полную стоимость нескольких запросов, строка остаётся
`unknown`.

## Reservations и admission control

После прохождения preflight/approval создаётся `council_cost_reservations` со
сроком жизни 30 минут. Reservation содержит fingerprint, ожидаемые токены и
стоимость. При сохранении результата она переводится в `settled` и связывается
с `run_id` и фактическим usage.

До нового запуска policy считает:

- завершённые Council runs текущего месяца;
- фактический или консервативный committed spend;
- активные reservations;
- фактические и зарезервированные токены;
- количество запусков и минутный admission rate.

## Workspace quotas

В `CouncilBudgetPolicy` добавлены:

- `monthly_run_limit`;
- `monthly_token_limit`;
- `per_minute_run_limit`;
- `routing_mode = off | advisory`;
- `routing_min_savings_percent`.

`0` отключает соответствующий limit.

## Budget-aware routing

`POST /api/council/cost/route` строит рекомендацию, но не запускает Совет и не
меняет состав без участия пользователя.

Правила текущей версии:

- provider сохраняется;
- рассматриваются только enabled models;
- `metadata.routing_disabled=true` исключает модель;
- участники остаются различными provider/model парами;
- выбирается известная более дешёвая стоимость;
- замена выполняется только при достижении `routing_min_savings_percent` либо
  когда текущий тариф неизвестен, а у кандидата известен;
- председатель оптимизируется отдельно.

Это intentionally advisory policy. Quality-aware scoring будет расширен на
этапе P2-006, где маршрутизация станет частью формальной оркестрации и арбитража.

## API

```tex
GET  /api/council/cost/usage
POST /api/council/cost/route
GET  /api/council/cost/policy
PUT  /api/council/cost/policy
POST /api/council/cost/estimate
POST /api/council/cost/approve


## Проверка

`verify_p2_005.bat` выполняет:

1. проверку Alembic head;
2. Council regressions, включая actual cost/reservations/quotas/router;
3. production frontend build.
