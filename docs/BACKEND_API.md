# AI Studio Enterprise Backend API

Версия: `v0.9.0`

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
  "model": "openrouter/free"
}
```

### AI Council

```text
GET  /api/council/status
POST /api/council/run
POST /api/council/live
GET  /api/council/live/{run_id}
GET  /api/council/live/{run_id}/events
DELETE /api/council/live/{run_id}
GET  /api/council/runs
GET  /api/council/runs/{run_id}
POST /api/council/runs/{run_id}/replay
POST /api/council/runs/{run_id}/replay/live
POST /api/council/runs/{run_id}/retry-failed
DELETE /api/council/runs/{run_id}
GET  /api/council/retention
PUT  /api/council/retention
POST /api/council/retention/purge
GET  /api/council/presets
POST /api/council/presets
GET  /api/council/cost/policy
PUT  /api/council/cost/policy
POST /api/council/cost/estimate
POST /api/council/cost/approve
GET  /api/council/cost/usage
POST /api/council/cost/route
```

Подробный формат:

- `docs/P2_001_AI_COUNCIL_API_UI.md`;
- `docs/P2_002_COUNCIL_HISTORY.md`;
- `docs/P2_003_LIVE_COUNCIL.md`;
- `docs/P2_004_COUNCIL_PRESETS_COST_CONTROL.md`;
- `docs/P2_005_COST_LEDGER_ROUTING.md`.
