# External Project Integration Strategy

Дата: **2026-07-24**

## Принцип

AI Studio Enterprise не должен становиться форком Claudexor или taOS. Мы используем сильные архитектурные идеи как ориентир, сохраняя собственную универсальную orchestration/control architecture и лицензионную независимость.

## Claudexor

Источник: https://github.com/razzant/claudexor

Полезные идеи для Council:

1. независимые варианты до синтеза;
2. явный PRIMARY/председатель;
3. независимый reviewer, не участвовавший в создании результата;
4. arbitration между конкурирующими решениями;
5. bounded delegation: ограничение глубины, количества подзадач и общего бюджета;
6. budget/quota ledger и preflight;
7. delta-context при переключении исполнителей;
8. для Code Workspace — Git worktrees, проверяемые patch-наборы и protected paths.

### Что уже перенесено концептуально в P2-004

- отдельный председатель;
- server-side preflight;
- Workspace budget policy;
- одноразовый approval gate;
- fingerprint-bound запуск;
- воспроизводимые составы и роли.

### Что переносим дальше

**P2-005**: actual-cost ledger, quotas и budget-aware routing.
**P2-006**: режимы Solo / Council / Best-of-N / Review / Arbitration / Delegate.
**P2-007**: Code Sandbox — worktrees, patch verification, protected paths.

## taOS

Источник: https://github.com/jaylfc/taOS

Рассматривается только как возможный внешний infrastructure backend для локального/распределённого исполнения. Встраивание исходного кода в проприетарный продукт не планируется без отдельного лицензионного решения. Предпочтительный вариант — adapter boundary через документированный API/CLI, если это будет совместимо с лицензией и коммерческой моделью.

## Уникальность AI Studio Enterprise

Конкурентное отличие строится не на самом факте «несколько моделей в одном окне», а на управляемой цепочке:

**задача → контекст/исследование → Council → план → выполнение → независимая проверка → Human Control → доказательства/аудит → результат → стоимость/лимиты → коммерческий outcome**.

Council становится универсальным decision engine для разных Workspaces, а не только инструментом разработки ПО.

## Запрет на слепое копирование

Перед переносом любого внешнего компонента обязательны:

- проверка лицензии;
- security review;
- dependency/SBOM review;
- определение adapter boundary;
- тесты воспроизводимости;
- собственная реализация product-specific policy и audit layer.
