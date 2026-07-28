# P1-018.6 — Mission Schedule Portfolio, Deadlines and Adaptive Cadence

## Назначение

Модуль управляет временем выполнения долгосрочных Mission. Он добавляет
часовые пояса, разрешённые дни, тихие часы, несколько временных окон,
контроль сроков и безопасные рекомендации по изменению частоты Mission Cycle.

## Безопасная конфигурация

По умолчанию Schedule Policy отсутствует и не меняет поведение Mission.
Созданная политика также безопасна: `enabled=false`, `schedule_mode=manual`,
`require_human_approval=true`, `auto_apply_enabled=false`,
`enforce_manual_cycles=false`, `overdue_action=observe`.

## Режимы

- `manual` — расписание хранится как операционный контекст, автоматические
  изменения не выполняются;
- `fixed` — используется фиксированный интервал и временные окна;
- `adaptive` — сервис рассчитывает рекомендуемый интервал по сроку,
  прогрессу и истории ошибок.

Автоматическое применение в adaptive-режиме возможно только одновременно при:

- `enabled=true`;
- `schedule_mode=adaptive`;
- `adaptive_enabled=true`;
- `auto_apply_enabled=true`;
- `require_human_approval=false`.

## Deadline surveillance

Deadline Scan создаёт события `approaching` и `overdue`. Политика
`overdue_action=pause` может приостановить активную Mission. Режимы `observe`
и `escalate` не изменяют статус Mission автоматически.

## Интеграция

Перед запуском Mission Cycle выполняется schedule admission. Planner получает:

- `context.mission_schedule`;
- `context.mission_schedule_admission`.

После каждого запланированного цикла `next_cycle_at` рассчитывается с учётом
интервала, часового пояса, разрешённых дней, тихих часов и Schedule Windows.
Все события `mission.schedule.*` и `mission.deadline.*` проходят через общий
Audit Trail и распределённый Event Transport.
