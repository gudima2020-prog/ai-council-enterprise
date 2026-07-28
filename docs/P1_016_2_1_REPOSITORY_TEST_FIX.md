# P1-016.2.1 — Repository test fix

## Причина

После внедрения Task State Machine поле `status` было намеренно удалено
из `TaskUpdate`.

Смена статуса теперь должна проходить только через:

```python
TaskStateMachine.transition(...)
```

Старый repository-тест продолжал вызывать:

```python
TaskUpdate(status=TaskStatus.QUEUED)
```

Pydantic проигнорировал лишнее поле, поэтому статус остался `created`.

## Исправление

Тест обновлён:

- переход `created -> queued` выполняется через TaskStateMachine;
- `TaskRepository.update()` проверяется только на обычных изменяемых полях;
- сохранена проверка runs, logs, artifacts и delete.

## Проверка

```powershell
.\run_tests.bat
```

Ожидается:

```text
58 passed
TEST RESULT: PASSED
```
