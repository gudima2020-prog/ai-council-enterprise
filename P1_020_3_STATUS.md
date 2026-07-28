# P1-020.3 — External Secret Providers, Rotation and Health Monitoring

Статус: **реализован и проверен**.
Alembic head: `20260716_0043`.

Добавлены:

- HashiCorp Vault KV v2;
- AWS Secrets Manager;
- Azure Key Vault;
- Google Secret Manager;
- rotation policies и история запусков;
- monitor-only, managed-random и external-handler rotation;
- provider/secret health checks и alerts;
- lifecycle scan и background monitoring;
- runtime-only secret injection без возврата plaintext через HTTP API.

Cloud SDK являются опциональными и импортируются только при использовании
соответствующего adapter. Vault использует стандартную библиотеку Python и
token из указанной environment variable.

Документация: `docs/P1_020_3_EXTERNAL_SECRET_PROVIDERS.md`.
