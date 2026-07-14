# AI Studio Enterprise Backend API

Версия: `v0.3`

## Запуск

```powershell
.\install.ps1
.\run_api.ps1
```

Или через CMD:

```cmd
install.bat
run_api.bat
```

## Проверка

После запуска откройте:

```text
http://127.0.0.1:8000/docs
```

## Основные endpoints

### Health

```text
GET /api/health
```

### Settings

```text
GET /api/settings
```

### Models

```text
GET /api/models
```

### Chat

```text
POST /api/chat
```

Пример тела запроса:

```json
{
  "message": "Привет! Ответь одной фразой.",
  "mode": "universal",
  "model": "deepseek/deepseek-chat-v3-0324"
}
```

## Следующий этап

Добавить React frontend и подключить его к этим endpoints.
