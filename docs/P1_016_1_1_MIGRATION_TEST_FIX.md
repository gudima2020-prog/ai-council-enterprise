# P1-016.1.1 — Migration test fix

Исправлен устаревший тест Alembic.

## Что изменено

Ранее тест жёстко ожидал ревизию:

```text
20260714_0001
```

После добавления Task Engine текущая head-ревизия стала:

```text
20260715_0002
```

Теперь тесты проверяют:

- существование `alembic.ini`;
- наличие ровно одной head-ревизии;
- совпадение `head_revision()` с реальной Alembic head;
- текущую ожидаемую ревизию Task Engine.

## Проверка

```powershell
.\run_tests.bat
```

Ожидается:

```text
50 passed
TEST RESULT: PASSED
```
