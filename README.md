# AI Council Enterprise

Универсальное приложение для работы с несколькими ИИ-моделями через OpenRouter.

## Цель проекта

Создать рабочую станцию ИИ для повседневных задач:

- универсальный чат;
- Совет ИИ из нескольких моделей;
- анализ криптовалютного рынка;
- помощь в программировании;
- подготовка документов и текстов;
- в будущем — работа с PDF, Word, Excel, изображениями и торговыми API.

## Текущая версия

`v0.1` — базовый каркас проекта.

В этой версии есть:

- структура проекта;
- безопасная работа с `.env`;
- подключение OpenRouter;
- консольный запуск;
- сохранение логов;
- установочные скрипты для Windows.

## Быстрый запуск на Windows

```powershell
cd C:\ai_council_enterprise
git clone https://github.com/gudima2020-prog/ai-council-enterprise.git .
git checkout v0.1-bootstrap
.\install.ps1
notepad .env
.\run.ps1
```

В файл `.env` нужно вставить ключ OpenRouter:

```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
OPENROUTER_DEFAULT_MODEL=deepseek/deepseek-chat-v3-0324
```

Если PowerShell запрещает запуск скриптов:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Можно использовать BAT-файлы:

```cmd
install.bat
run.bat
```

## Важно

Не публикуйте настоящий `.env` и API-ключи в GitHub или чатах.
