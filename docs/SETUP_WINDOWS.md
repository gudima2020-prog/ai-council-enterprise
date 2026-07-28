# Установка AI Studio Enterprise на Windows

## Требования

- Windows 10/11;
- Python 3.11+;
- Node.js `20.19+` или `22.12+`;
- доступ в интернет во время установки;
- OpenRouter API key.

Проверьте:

```cmd
python --version
node --version
npm --version
```

## Новая установка

1. Распакуйте архив в путь без системных ограничений, например:
   `C:\Projects\ai-council-enterprise`.
2. Запустите:

```cmd
install.bat
```

3. Откройте `.env`:

```cmd
notepad .env
```

4. Укажите ключ:

```env
OPENROUTER_API_KEY=<YOUR_OPENROUTER_API_KEY>
OPENROUTER_DEFAULT_MODEL=openrouter/free
```

5. Создайте/обновите базу и проверьте проект:

```cmd
db_upgrade.bat
run_tests.bat
```

`run_tests.bat` использует отдельную временную SQLite-базу. Рабочая
`data\ai_studio.db` во время тестов не открывается и не изменяется.

6. Запустите backend и UI:

```cmd
start_studio.bat
```

Откройте <http://127.0.0.1:5173>.

## Обновление существующей установки

1. Остановите backend и frontend.
2. Сделайте копию каталога проекта, особенно `.env` и `data/`.
3. Распакуйте обновлённый архив поверх существующего проекта.
4. Выполните:

```cmd
install.bat
db_upgrade.bat
run_tests.bat
start_studio.bat
```

Архив обновления не содержит `.env`, баз и логов, поэтому обычная распаковка
поверх каталога их не удаляет и не заменяет.

## Раздельный запуск

В первом окне:

```cmd
run_api.bat
```

Во втором:

```cmd
run_frontend.bat
```

## Проверка миграции

```cmd
db_current.bat
```

Ожидаемый revision:

```text
20260724_0048
```

Не выполняйте `alembic stamp head` вручную. `db_upgrade.bat` проверяет схему
до stamping и останавливается при частичной/неоднозначной базе.

При обновлении до v0.7.0 шаг `db_upgrade.bat` обязателен: revision 0045
разрешает сохранение отменённых запусков Council. Существующие рабочие данные
не удаляются.

## Частые ошибки

### `.env` не найден

Проверьте, что файл называется `.env`, а не `.env.txt`. При первой установке
он копируется из `.env.example`.

### Модели не загружаются

Убедитесь, что backend работает на `127.0.0.1:8000`, затем откройте
<http://127.0.0.1:8000/api/health>.

### Model unavailable

Бесплатные модели OpenRouter меняются. Оставьте `openrouter/free` как
резервный маршрут либо обновите модель через API model catalog.

### Node.js/npm not found

Установите поддерживаемую версию Node.js, повторно запустите `install.bat`.

### PowerShell блокирует скрипты

BAT-файлы уже запускают миграционные PowerShell-скрипты с локальным
`ExecutionPolicy Bypass`. Глобально менять policy обычно не требуется.
