# P1-018.3 — Mission Risk Register, Hypotheses and Decision Checkpoints

## Назначение

Этап добавляет контур управления неопределённостью долгосрочной Mission:
реестр рисков, проверяемые гипотезы и формальные контрольные точки принятия
решений. Planner получает не только память Mission, но и актуальный governance
context, а Mission Cycle не может незаметно пройти через нерешённый блокирующий
Checkpoint.

## Безопасность по умолчанию

- новый Checkpoint имеет `requires_human = true`;
- `auto_decision_enabled = false`;
- автоматизация разрешена только для решений `continue` и `accept_risk`;
- автоматическое решение требует явного opt-in и достижения порога уверенности;
- `high` и `critical` Risk создают блокирующий Checkpoint;
- Mission Cycle блокируется, пока Checkpoint находится в `ready`;
- обход блокировки возможен только через `force = true` и передаётся в
  `context.mission_cycle_admission`.

## Risk Register

Risk хранит:

- стабильный `risk_key` внутри Mission;
- категорию и владельца;
- вероятность и влияние в процентах;
- рассчитанный exposure score;
- severity `low`, `medium`, `high`, `critical`;
- mitigation и contingency plan;
- индикаторы материализации;
- сроки проверки и закрытия;
- флаг обязательного Checkpoint.

Exposure рассчитывается детерминированно:

```text
exposure = probability_percent × impact_percent / 100
```

Пороговые значения:

```text
0–24.99   low
25–49.99  medium
50–74.99  high
75–100    critical
```

Каждая оценка сохраняется отдельной записью
`mission_risk_assessments` с предыдущим и новым статусом, обоснованием,
индикаторами и субъектом решения.

## Hypothesis Register

Hypothesis описывает проверяемое предположение Mission:

- statement и rationale;
- test plan;
- success/failure criteria;
- требуемое число Evidence;
- пороги support/reject;
- текущую и целевую confidence;
- связанные Evidence;
- необходимость Checkpoint при неопределённом результате.

Автоматическая оценка использует Evidence с директивами:

```json
{
  "hypothesis_effect": "support"
}
```

или:

```json
{
  "supports_hypothesis": true
}
```

Вес Evidence определяется `aggregate_score`. Результат:

- `supported`;
- `rejected`;
- `inconclusive`;
- `invalidated`.

История оценок хранится в `mission_hypothesis_evaluations`.

## Decision Checkpoints

Типы Checkpoint:

- `manual`;
- `scheduled`;
- `risk`;
- `hypothesis`;
- `deadline`;
- `budget`;
- `phase_gate`.

Статусы:

- `pending` — ожидает срока или условия;
- `ready` — требует решения и может блокировать Cycle;
- `resolved` — решение принято;
- `deferred` — отложен до нового срока;
- `expired` — истёк;
- `cancelled` — отменён.

Решения:

- `continue`;
- `pause`;
- `replan`;
- `cancel`;
- `accept_risk`;
- `request_review`;
- `defer`.

Решение `pause` переводит Mission в `paused`, `cancel` — в `cancelled`,
`replan` ставит ближайший цикл на текущее время и фиксирует запрос в metadata,
а `accept_risk` обновляет связанный Risk.

## Интеграция с Mission Runtime

Перед `_prepare_cycle_for_planning` выполняется admission guard:

1. создаются отсутствующие Checkpoints для high/critical Risk;
2. наступившие `pending/deferred` Checkpoints переводятся в `ready`;
3. ищутся блокирующие `ready` Checkpoints;
4. при наличии блокировки запуск завершается `MissionStateError`;
5. публикуется событие `mission.cycle.blocked_by_checkpoint`.

В Planner context передаются:

```text
context.mission_governance
context.mission_cycle_admission
```

## Автоматическая регистрация сбоев

Событие `mission.cycle.failed` создаёт или обновляет operational Risk
`cycle_failure.<cycle_id>`. Он получает высокую вероятность, high severity и
блокирующий Checkpoint для разбирательства или replanning.

## API

Диагностика:

- `GET /api/mission-governance/status`;
- `GET /api/missions/{mission_id}/governance-context`;
- `GET /api/missions/{mission_id}/governance-dashboard`.

Risk Register:

- `POST /api/missions/{mission_id}/risks`;
- `GET /api/missions/{mission_id}/risks`;
- `GET|PATCH /api/mission-risks/{risk_id}`;
- `POST /api/mission-risks/{risk_id}/assess`;
- `POST /api/mission-risks/{risk_id}/materialize`;
- `POST /api/mission-risks/{risk_id}/close`;
- `GET /api/mission-risks/{risk_id}/assessments`.

Hypotheses:

- `POST /api/missions/{mission_id}/hypotheses`;
- `GET /api/missions/{mission_id}/hypotheses`;
- `GET|PATCH /api/mission-hypotheses/{hypothesis_id}`;
- `POST /api/mission-hypotheses/{hypothesis_id}/evaluate`;
- `POST /api/mission-hypotheses/{hypothesis_id}/reopen`;
- `GET /api/mission-hypotheses/{hypothesis_id}/evaluations`.

Decision Checkpoints:

- `POST /api/missions/{mission_id}/checkpoints`;
- `GET /api/missions/{mission_id}/checkpoints`;
- `POST /api/missions/{mission_id}/checkpoints/scan`;
- `GET /api/mission-checkpoints/{checkpoint_id}`;
- `POST /api/mission-checkpoints/{checkpoint_id}/evaluate`;
- `POST /api/mission-checkpoints/{checkpoint_id}/decide`;
- `POST /api/mission-checkpoints/{checkpoint_id}/cancel`;
- `GET /api/mission-checkpoints/{checkpoint_id}/decisions`.

## База данных

Alembic revision: `20260716_0023`.

Новые таблицы:

- `mission_risks`;
- `mission_risk_assessments`;
- `mission_hypotheses`;
- `mission_hypothesis_evaluations`;
- `mission_decision_checkpoints`;
- `mission_checkpoint_decisions`.
