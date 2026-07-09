# Установка на Windows

## 1. Клонирование

```powershell
cd C:\ai_council_enterprise
git clone https://github.com/gudima2020-prog/ai-council-enterprise.git .
```

Если папки нет:

```powershell
mkdir C:\ai_council_enterprise
cd C:\ai_council_enterprise
git clone https://github.com/gudima2020-prog/ai-council-enterprise.git .
```

## 2. Установка

```powershell
.\install.ps1
```

Если PowerShell блокирует скрипты:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. API-ключ

Откройте `.env`:

```powershell
notepad .env
```

Вставьте ключ OpenRouter:

```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
OPENROUTER_DEFAULT_MODEL=deepseek/deepseek-chat-v3-0324
```

## 4. Запуск

```powershell
.\run.ps1
```

Или через CMD:

```cmd
run.bat
```

## Частые ошибки

### Не найден OPENROUTER_API_KEY

Проверьте, что файл называется именно `.env`, а не `.env.txt`.

### Model unavailable

Откройте `.env` и замените `OPENROUTER_DEFAULT_MODEL` на доступную модель OpenRouter.

### Недостаточно средств

Пополните баланс OpenRouter или выберите бесплатную доступную модель, если она есть на аккаунте.
