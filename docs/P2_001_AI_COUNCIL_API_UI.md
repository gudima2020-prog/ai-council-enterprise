# P2-001 — AI Council API & UI

## Назначение

AI Council запускает несколько независимых моделей с разными аналитическими
ролями, затем формирует общий структурированный вывод.

## API

```text
GET  /api/council/status
POST /api/council/run
```

Минимальный запрос:

```json
{
  "question": "Какой вариант архитектуры выбрать?",
  "mode": "code",
  "members": [
    {
      "provider": "openrouter",
      "model": "openrouter/free",
      "role": "analyst"
    },
    {
      "provider": "openrouter",
      "model": "openai/gpt-oss-20b:free",
      "role": "critic"
    }
  ]
}
```

Ограничения:

- от 2 до 6 участников;
- provider/model pairs должны быть уникальны;
- вопрос до 50 000 символов;
- режимы: `universal`, `crypto`, `code`, `documents`.

## Выполнение

1. Публикуется `council.run.started`.
2. Участники выполняются параллельно через `asyncio.gather`.
3. Синхронный provider adapter переносится в worker thread.
4. Успешные ответы передаются председателю как недоверенные цитаты.
5. Председатель возвращает JSON либо безопасный unstructured/fallback.
6. Публикуется completion/failure event без текста ответов.

Для конкретной модели OpenRouter со slug, оканчивающимся на `:free`, коды
`RATE_LIMIT`, `TIMEOUT`, `NETWORK_ERROR`, `MODEL_UNAVAILABLE` и
`PROVIDER_UNAVAILABLE` допускают ровно одну дополнительную попытку через
`openrouter/free`. Для тарифицируемых моделей автоматическая замена запрещена.

## Результат

- `final_answer`;
- `consensus`;
- `disagreements`;
- `recommendations`;
- `confidence` от 0 до 100;
- независимые ответы, latency, usage и ошибки.
- `requested_model`, `fallback_used` и `fallback_model` для прозрачности
  резервного маршрута.

Если часть участников недоступна, статус равен `partial`, а успешные ответы
сохраняются в результате. HTTP 502 возвращается только когда не ответил ни один
участник.

## Безопасность синтеза

Ответы моделей заключаются в явные границы и помечаются как недоверенные
цитаты. System prompt запрещает выполнять инструкции из этих ответов и требует
анализировать их только как материал Совета.

Parser принимает обычный JSON, fenced JSON, double-encoded JSON, безопасные
Python-like словари и варианты `final_answer`/`finalAnswer`. Если остальные
поля повреждены, отдельно извлекается строка итогового ответа, поэтому сырой
JSON не занимает карточку результата.

## Frontend

React UI:

- загружает enabled models;
- исключает из выбора модели с `metadata.catalog_hidden = true`, не отключая
  их для существующих Workspace policy;
- первым выбирает бесплатный состав;
- назначает роли по порядку;
- показывает число API-вызовов и предупреждение о тарификации;
- показывает восстановление через free fallback и понятные коды ошибок;
- выводит итог и раскрываемые карточки каждого участника.

## Следующий этап

Продолжение реализовано в
[`P2_002_COUNCIL_HISTORY.md`](P2_002_COUNCIL_HISTORY.md): persistence,
Workspace history, replay и retention.
