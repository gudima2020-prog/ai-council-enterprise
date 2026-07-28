# P2-004 — Council Presets & Cost Control

Версия: **v0.8.0**
Alembic revision: **`20260724_0046`**

## Цель

Сделать Council воспроизводимым и безопасным по расходам: состав должен быть повторяемым, председатель — явным, а стоимость — проверяться сервером до первого provider request.

## Presets

Preset хранит название, описание, режим, 2–6 участников, роль каждого участника, timeout и отдельные provider/model председателя. Presets изолируются по Workspace и доступны через `/api/council/presets`.

## Cost prefligh

`POST /api/council/cost/estimate` строит консервативную оценку input/output tokens и вычисляет стоимость из metadata модели:

- `billing=free` — стоимость 0 известна;
- `billing=metered` + `pricing.input_per_million_usd` / `output_per_million_usd` — стоимость известна;
- тарифицируемая модель без прайса — `partial/unknown`, а не 0.

## Workspace policy

`GET/PUT /api/council/cost/policy`:

- `monthly_budget_usd`;
- `per_run_soft_limit_usd`;
- `per_run_hard_limit_usd`;
- `approval_threshold_usd`;
- `unknown_cost_policy = allow | require_approval | block`.

Нулевой лимит отключает соответствующее ограничение.

## Approval

При `approval_required` клиент получает estimate и должен явно создать approval через `POST /api/council/cost/approve`.

Approval:

- живёт 15 минут;
- одноразовый;
- привязан к Workspace;
- привязан к SHA-256 fingerprint полного Council request;
- не может быть перенесён на другой вопрос, состав, роли, председателя или timeout.

Replay и retry failed используют тот же механизм через специальные approval endpoints.

## Аудит

`council_runs` хранит:

- `estimated_cost_usd`;
- `cost_estimate_status`;
- `cost_approval_id`.

Следующий шаг P2-005 — считать actual cost по фактическому usage и использовать budget ledger для динамической маршрутизации.
