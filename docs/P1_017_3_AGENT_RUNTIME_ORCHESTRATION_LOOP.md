# P1-017.3 — Agent Runtime and Orchestration Loop

## Реализовано

- фактический запуск Execution Plan;
- асинхронный orchestration loop;
- параллельное выполнение независимых шагов;
- ограничение `max_parallel_steps`;
- persistent `ExecutionStepRun` для каждой попытки;
- timeout и retry каждого шага;
- автоматическое назначение Agent перед запуском;
- лимит конкурентности конкретного Agent;
- registry исполнителей Agent и Tool;
- встроенные Agent executors;
- встроенные Tools: `echo`, `merge`, `select`, `sleep`;
- передача output предыдущего шага во вход следующего;
- шаблоны `$steps.<key>.output.<field>`;
- шаблоны `{{ steps.<key>.output.<field> }}`;
- условный пропуск шага;
- выполнение `agent`, `tool`, `task`, `approval`, `decision`, `checkpoint`;
- отмена выполняющегося Plan;
- восстановление прерванных Plan после перезапуска;
- runtime diagnostics и история step runs;
- события `execution_plan.runtime.*` и `execution_plan.step.*`;
- миграция `20260715_0012`.

## Новая таблица

```text
execution_step_runs
```

Каждая попытка содержит:

```text
plan_id
step_id
step_key
attempt
status
agent_id
executor_ref
input_json
output_json
error
duration_ms
started_at
finished_at
```

## Новые endpoints

```text
GET  /api/execution-runtime/status
POST /api/execution-runtime/recover

POST /api/execution-plans/{plan_id}/run
POST /api/execution-plans/{plan_id}/cancel
GET  /api/execution-plans/{plan_id}/runtime
GET  /api/execution-plans/{plan_id}/step-runs
```

## Запуск Plan

```json
{
  "auto_validate": true,
  "auto_assign": true,
  "strict_assignment": true,
  "replace_assignments": false,
  "wait": false,
  "wait_timeout_seconds": 30
}
```

При `wait: false` API сразу возвращает состояние Plan, а выполнение
продолжается в фоне.

При `wait: true` запрос ожидает завершения Plan в пределах
`wait_timeout_seconds`.

## Передача результатов

```json
{
  "source": "$steps.collect.output.value",
  "label": "Результат: {{ steps.collect.output.value }}"
}
```

Точное значение с `$...` сохраняет исходный JSON-тип. Шаблон `{{ ... }}`
вставляет значение как строку.

## Built-in Agent executors

```text
builtin.planner
builtin.analyst
builtin.writer
builtin.worker
```

Внешний Plugin сможет зарегистрировать собственный async executor через
`ExecutionPlanRuntime.registry.register_agent()`.

## Built-in Tools

```text
echo
merge
select
sleep
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0012
```

## Следующий этап

P1-017.4 — Tool Registry, permission scopes and sandboxed tool execution.
