AI Studio Enterprise v0.3 Backend patch

Что добавлено:
- FastAPI backend;
- endpoint /api/health;
- endpoint /api/settings;
- endpoint /api/models;
- endpoint /api/chat;
- запуск API через run_api.bat / run_api.ps1;
- документация docs/BACKEND_API.md.

Установка:

1. Распакуйте архив поверх:
   C:\Projects\ai-council-enterprise

2. В PowerShell или CMD из папки проекта:
   .\install.bat

3. Запуск backend:
   .\run_api.bat

4. Проверка в браузере:
   http://127.0.0.1:8000/docs

Это основа новой архитектуры FastAPI + React + Tauri.
