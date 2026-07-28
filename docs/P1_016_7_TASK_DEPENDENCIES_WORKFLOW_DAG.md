# P1-016.7 — Task Dependencies, DAG and Workflow Execution

## Реализовано

- persistent-таблица `task_dependencies`;
- hard и soft зависимости;
- обязательный статус upstream Task;
- запрет self-dependency;
- запрет зависимостей между разными Workspace;
- обнаружение циклов до записи;
- проверка DAG;
- топологическая сортировка;
- dependency-aware enqueue;
- автоматический статус `waiting`;
- каскадный failure для hard dependency;
- продолжение workflow после soft dependency failure;
- автоматический запуск зависимых Task;
- reconciliation после перезапуска;
- граф, сводка и диагностика workflow;
- миграция `20260715_0004`.

## Семантика

Hard dependency:

```text
A completed -> B запускается
A failed/cancelled -> B получает failed
```

Soft dependency:

```text
A completed/failed/cancelled -> B может запускаться
```

## Endpoints

```text
POST   /api/tasks/{task_id}/dependencies
GET    /api/tasks/{task_id}/dependencies
DELETE /api/tasks/{task_id}/dependencies/{depends_on_task_id}

GET  /api/task-workflows/status
GET  /api/task-workflows/{task_id}/graph
POST /api/task-workflows/{task_id}/validate
POST /api/task-workflows/{task_id}/start
POST /api/task-workflows/reconcile
```

Обычный endpoint:

```text
POST /api/tasks/{task_id}/enqueue
```

теперь также проверяет зависимости.

## Пример зависимости

```json
{
  "depends_on_task_id": "task_collect",
  "dependency_type": "hard",
  "required_status": "completed"
}
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая миграция:

```text
20260715_0004
```

## Следующий этап

P1-016.8 — Workflow templates, conditional branches and result mapping.
