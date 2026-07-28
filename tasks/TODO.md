# TODO

## P2-002 — Council Run Persistence & History

- [x] Добавить Alembic revision `20260716_0044`.
- [x] Создать нормализованные таблицы `council_runs` и
  `council_run_members`.
- [x] Сохранять completion/failure и частичные результаты атомарно.
- [x] Добавить API списка, карточки, удаления и повторного запуска.
- [x] Добавить историю и фильтры Workspace в React UI.
- [x] Добавить retention policy для текстов и token usage.

## P2-003 — Live Progress & Streaming

- [x] SSE-прогресс участников с буфером и переподключением.
- [x] Потоковая выдача итогового синтеза.
- [x] Отмена запуска и timeout на участника.
- [x] Повтор только неуспешных участников.

## P2-004 — Council Presets & Cost Control

- [x] Сохраняемые составы и роли.
- [x] Лимит токенов и ориентировочная стоимость до запуска.
- [x] Бюджеты Workspace и approval для дорогих/неизвестных запусков.
- [x] Выбор отдельной модели-председателя.
- [x] Cost gate для replay и retry failed.

## P2-005 — Production Hardening & Routing

- [ ] Actual-cost settlement по фактическому token usage.
- [ ] Budget-aware routing и provider quotas.
- [ ] API authentication для пользовательских маршрутов.
- [ ] Rate limiting и request size policy.
- [ ] Provider/model catalog refresh с безопасным diff.
- [ ] Packaging desktop runtime и Tauri shell.

## Позже

- Documents/PDF/OCR;
- Research и Browser;
- Crypto AI;
- Code Workspace;
- визуальный Plugin Manager.
