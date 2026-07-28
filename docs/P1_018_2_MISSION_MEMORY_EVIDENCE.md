# P1-018.2 — Mission Memory, Evidence Evaluation and Goal Confirmation

## Назначение

Этап добавляет долговременную память Mission и формальный контур подтверждения
достижения целей. Система больше не обязана считать Goal выполненной только по
заявленному прогрессу: достижение можно связать с проверяемыми доказательствами,
политикой качества и отдельным решением о подтверждении.

## Безопасность по умолчанию

Политика Evidence по умолчанию консервативна:

- `require_human_review = true`;
- `auto_confirm_enabled = false`;
- автоматическое завершение Goal невозможно без явной настройки;
- автоматическое подтверждение выполняется только для active Mission;
- при автоматическом подтверждении учитываются только Evidence со статусом
  `accepted`.

## Mission Memory

Memory Entry имеет стабильный `memory_key`, версию, категорию, источник,
важность и уверенность. Повторная запись по тому же ключу не создаёт дубликат,
а повышает `version`.

Поддерживаемые категории:

- `fact`;
- `decision`;
- `artifact`;
- `lesson`;
- `constraint`;
- `summary`.

Неархивированная и непросроченная память автоматически передаётся Planner
Service в `context.mission_memory` при создании очередного Mission Cycle.

## Evidence

Поддерживаемые типы:

- `artifact`;
- `metric`;
- `execution_result`;
- `document`;
- `external_reference`;
- `human_attestation`;
- `test_result`.

Evidence дедуплицируется по SHA-256 канонического JSON внутри одной Goal.
Встроенный детерминированный evaluator рассчитывает:

- полноту и структуру;
- проверяемость и трассируемость;
- релевантность критериям Goal;
- итоговый aggregate score.

Результат может быть `accepted`, `rejected` или `needs_review`.

## Автоматическое подтверждение

Политика Goal задаёт:

- минимальное число Evidence;
- минимальную индивидуальную оценку;
- минимальную среднюю оценку;
- необходимость независимых источников;
- допустимые типы Evidence;
- обязательность ручной проверки;
- разрешение автоматического подтверждения.

При выполнении условий Goal переводится в `achieved`, Mission пересчитывает
взвешенный прогресс, разблокирует зависимые Goals и завершается, когда
подтверждены все Goals.

Каждое подтверждение или повторное открытие фиксируется отдельной записью
`mission_goal_confirmations` и событием `mission.goal.*`.

## Автоматический сбор

После `execution_plan.runtime.completed` сервис:

1. находит связанный Mission Cycle;
2. создаёт Evidence типа `execution_result` для Goals цикла;
3. сохраняет итог цикла в Mission Memory;
4. запускает оценку Evidence;
5. применяет auto-confirm только при явно разрешающей политике.

## API

Основные маршруты:

- `GET /api/mission-memory/status`;
- `PUT /api/missions/{mission_id}/memory/{memory_key}`;
- `GET /api/missions/{mission_id}/memory`;
- `GET /api/missions/{mission_id}/memory-context`;
- `DELETE /api/missions/{mission_id}/memory/{memory_id}`;
- `GET|PUT /api/missions/{mission_id}/goals/{goal_id}/evidence-policy`;
- `POST /api/missions/{mission_id}/goals/{goal_id}/evidence`;
- `GET /api/missions/{mission_id}/evidence`;
- `POST /api/mission-evidence/{evidence_id}/evaluate`;
- `POST /api/mission-evidence/{evidence_id}/review`;
- `POST /api/missions/{mission_id}/goals/{goal_id}/evaluate-evidence`;
- `POST /api/missions/{mission_id}/goals/{goal_id}/confirm`;
- `POST /api/missions/{mission_id}/goals/{goal_id}/reopen`;
- `GET /api/missions/{mission_id}/goal-confirmations`.

## База данных

Alembic revision: `20260716_0022`.

Новые таблицы:

- `mission_memory_entries`;
- `mission_evidence_policies`;
- `mission_evidence`;
- `mission_goal_confirmations`.
