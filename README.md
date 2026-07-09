# AI Council Enterprise

Универсальное настольное приложение для работы с несколькими ИИ-моделями через OpenRouter.

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

В этой версии закладываются:

- структура проекта;
- безопасная работа с `.env`;
- подключение OpenRouter;
- консольный тестовый запуск;
- подготовка к графическому интерфейсу.

## Быстрый запуск на Windows

```powershell
cd C:\ai_council_enterprise
git clone https://github.com/gudima2020-prog/ai-council-enterprise.git .
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
python main.py
```

В файл `.env` нужно вставить ключ OpenRouter:

```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
```

## Важно

Не публикуйте настоящий `.env` и API-ключи в GitHub или чатах.
