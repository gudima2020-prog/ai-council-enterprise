# React frontend v0.4

## Запуск backend

В первом окне PowerShell/CMD:

```cmd
run_api.bat
```

Backend должен быть доступен:

```text
http://127.0.0.1:8000/docs
```

## Запуск frontend

Во втором окне:

```cmd
run_frontend.bat
```

Откройте:

```text
http://127.0.0.1:5173
```

## Что работает

- React + TypeScript + Vite;
- чат-интерфейс;
- выбор режима;
- отправка запросов в FastAPI endpoint `/api/chat`;
- отображение ответа.

## Дальше

Следующий этап — Tauri desktop wrapper.
