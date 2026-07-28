# P1-016.8 — Workflow Templates, Conditions and Result Mapping

## Реализовано

- постоянные шаблоны workflow;
- версионирование шаблонов;
- валидация структуры до сохранения;
- создание экземпляра workflow из шаблона;
- автоматическое создание Task и зависимостей;
- обязательные параметры и значения по умолчанию;
- условные ветви `all`, `any`, `not`;
- операторы `eq`, `ne`, `exists`, `truthy`, `falsy`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`;
- статус Task `skipped` для невыбранной ветви;
- передача входных данных в payload;
- передача результатов предыдущих узлов в payload следующих узлов;
- постоянные Workflow Instance;
- состояние и контекст экземпляра;
- миграция `20260715_0005`.

## Новые таблицы

```text
workflow_templates
workflow_instances
```

## Новые endpoints

```text
POST /api/workflow-templates
GET  /api/workflow-templates
GET  /api/workflow-templates/{template_id}
POST /api/workflow-templates/{template_id}/validate
POST /api/workflow-templates/{template_id}/enable
POST /api/workflow-templates/{template_id}/disable
POST /api/workflow-templates/{template_id}/instantiate

GET  /api/workflow-instances/{instance_id}
POST /api/workflow-instances/{instance_id}/start
```

## Условие

```json
{
  "path": "nodes.analyze.result.approved",
  "operator": "eq",
  "value": true
}
```

Группы условий:

```json
{
  "all": [
    {"path": "input.confirmed", "operator": "eq", "value": true},
    {"path": "nodes.analyze.result.risk", "operator": "lt", "value": 50}
  ]
}
```

## Result mapping

Ключ — путь назначения внутри payload текущей Task.
Значение — путь к данным экземпляра workflow.

```json
{
  "pair": "input.pair",
  "analysis": "nodes.analyze.result",
  "decision.score": "nodes.evaluate.result.score"
}
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0005
```

## Следующий этап

P1-016.9 — Workflow approvals, human confirmation gates and resumable waits.
