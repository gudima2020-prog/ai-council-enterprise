# P1-017.2 — Agent Registry, Capabilities and Selection Policy

## Реализовано

- постоянный реестр Agent;
- глобальные и Workspace-специфичные Agent;
- роли, инструменты и capabilities;
- proficiency от 0 до 100;
- status и enabled-флаг;
- приоритет Agent;
- max concurrency и расчёт текущей нагрузки;
- автоматическое ранжирование кандидатов;
- автоматическое назначение Agent на отдельный шаг;
- пакетное назначение на весь Execution Plan;
- strict и partial assignment;
- ручное назначение и force override;
- очистка назначения;
- сохранение score и причин выбора;
- четыре встроенных профиля Agent;
- события `agent.*` и `execution_plan.agent.*`;
- подключение событий к Immutable Audit Trail;
- миграция `20260715_0011`.

## Таблицы

```text
agent_profiles
agent_capabilities
```

В `execution_plan_steps` добавлены:

```text
assigned_agent_id
assignment_json
assigned_at
```

## Встроенные Agent

```text
builtin.planner
builtin.analyst
builtin.writer
builtin.worker
```

Они создаются идемпотентно при запуске приложения.

## Алгоритм выбора

Кандидат должен:

1. быть enabled;
2. иметь status `available`;
3. соответствовать Workspace или быть глобальным;
4. иметь требуемую роль;
5. иметь требуемую capability;
6. иметь необходимые tools;
7. иметь свободную concurrency capacity.

Итоговый score учитывает:

- совпадение роли;
- proficiency capability;
- точное совпадение Workspace;
- приоритет Agent;
- свободную capacity;
- наличие необходимых tools.

При равном score используется стабильная сортировка по `agent_key`.

## Endpoints

```text
POST   /api/agents
GET    /api/agents
POST   /api/agents/match
GET    /api/agents/{agent_id}
PATCH  /api/agents/{agent_id}
DELETE /api/agents/{agent_id}

POST   /api/agents/{agent_id}/capabilities
PATCH  /api/agents/{agent_id}/capabilities/{capability_id}
DELETE /api/agents/{agent_id}/capabilities/{capability_id}

GET  /api/execution-plans/{plan_id}/agent-assignments
POST /api/execution-plans/{plan_id}/assign-agents

GET    /api/execution-plans/{plan_id}/steps/{step_id}/agent-selection
POST   /api/execution-plans/{plan_id}/steps/{step_id}/assign-agent
DELETE /api/execution-plans/{plan_id}/steps/{step_id}/assigned-agent
```

## Пример Agent

```json
{
  "workspace_id": null,
  "agent_key": "rentahuman.task-analyst",
  "display_name": "RentAHuman Task Analyst",
  "description": "Оценивает выполнимость, риск и экономику задания.",
  "roles": ["analyst"],
  "tools": ["web_search"],
  "enabled": true,
  "status": "available",
  "priority": 90,
  "max_concurrency": 2,
  "executor_ref": "rentahuman.analysis-agent",
  "model_slug": null,
  "metadata": {},
  "capabilities": [
    {
      "name": "task_analysis",
      "proficiency": 95,
      "enabled": true,
      "metadata": {}
    },
    {
      "name": "risk_assessment",
      "proficiency": 90,
      "enabled": true,
      "metadata": {}
    }
  ]
}
```

## Автоматическое назначение всего плана

```json
{
  "replace_existing": false,
  "strict": true
}
```

`strict=true` запрещает частичное назначение. Если хотя бы для одного
agent-step кандидат не найден, изменения не применяются.

## Ручное назначение

```json
{
  "agent_id": "agent_...",
  "force": false,
  "reason": "Исполнитель выбран владельцем проекта."
}
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0011
```

## Следующий этап

P1-017.3 — Agent runtime, step execution and orchestration loop.
