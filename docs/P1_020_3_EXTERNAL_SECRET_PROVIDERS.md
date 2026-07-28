# P1-020.3 — External Secret Providers

## Поддерживаемые adapters

| Adapter key | Provider | Основная конфигурация |
|---|---|---|
| `hashicorp_vault_kv2` | Vault KV v2 | `address`, `mount`, `auth_env`, `namespace` |
| `aws_secrets_manager` | AWS Secrets Manager | `region_name`, optional `endpoint_url` |
| `azure_key_vault` | Azure Key Vault | `vault_url` |
| `gcp_secret_manager` | Google Secret Manager | `project_id` |

Конфигурация provider не принимает token/password/API key. Чувствительный
материал должен оставаться в environment, cloud identity или `secret://`
reference.

## Опциональные зависимости

```cmd
install_secret_provider_sdks.bat
```

Устанавливает пакеты из `requirements-secret-providers.txt`. Vault adapter
дополнительных SDK не требует.

## Аутентификация

- Vault: token читается из environment variable, по умолчанию `VAULT_TOKEN`;
- AWS: стандартная credential chain `boto3`;
- Azure: `DefaultAzureCredential`;
- GCP: Application Default Credentials.

Никакие credential values не сохраняются в `config_json`.

## Provider references

Vault и JSON-backed providers используют формат:

```text
path-or-secret-id#field
```

Если `#field` отсутствует, применяется `value_field` из public config либо
`value`.

## Lifecycle

- provider health check;
- rotation policy на secret;
- ручной или scheduled rotation run;
- optional approval;
- health scan с предупреждением об истечении;
- deduplicated alerts и автоматическое закрытие после восстановления.

Режимы rotation:

- `monitor_only`;
- `managed_random`;
- `external_handler`.

## API

Основные маршруты:

```text
POST /api/secret-providers
GET  /api/secret-providers
POST /api/secret-providers/{id}/health
PUT  /api/secrets/{id}/rotation-policy
POST /api/secrets/{id}/rotation-runs
POST /api/secret-lifecycle/scan
GET  /api/secret-health-checks
GET  /api/secret-health-alerts
```

Маршруты защищены permissions Human Control Center.
