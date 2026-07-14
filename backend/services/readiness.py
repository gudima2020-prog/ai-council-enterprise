from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.core.config import AppSettings
from backend.database.session import engine, session_scope
from backend.plugins.registry import plugin_loader
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspace_state import ACTIVE_WORKSPACE_KEY


class ReadinessService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def check(self) -> dict[str, Any]:
        checks = {
            "database": self._check_database(),
            "models": self._check_models(),
            "plugins": self._check_plugins(),
            "active_workspace": self._check_active_workspace(),
        }

        ready = all(
            item["status"] in {"ok", "not_configured"}
            for item in checks.values()
        )

        return {
            "status": "ready" if ready else "degraded",
            "ready": ready,
            "app_name": self._settings.app_name,
            "app_version": self._settings.app_version,
            "environment": self._settings.environment,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
        }

    @staticmethod
    def _check_database() -> dict[str, Any]:
        try:
            with engine.connect() as connection:
                result = connection.execute(text("SELECT 1")).scalar_one()

            return {
                "status": "ok",
                "details": {"select_1": result},
            }
        except Exception as exc:
            return {
                "status": "error",
                "details": {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }

    @staticmethod
    def _check_models() -> dict[str, Any]:
        try:
            with session_scope() as session:
                enabled_models = ModelRepository(session).list_enabled()

            if not enabled_models:
                return {
                    "status": "error",
                    "details": {"enabled_models": 0},
                }

            return {
                "status": "ok",
                "details": {
                    "enabled_models": len(enabled_models),
                    "slugs": [item.slug for item in enabled_models],
                },
            }
        except Exception as exc:
            return {
                "status": "error",
                "details": {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }

    @staticmethod
    def _check_plugins() -> dict[str, Any]:
        try:
            plugins = plugin_loader.list_plugins()
            failed = [
                item for item in plugins
                if item.get("status") == "failed"
            ]

            return {
                "status": "error" if failed else "ok",
                "details": {
                    "total": len(plugins),
                    "failed": len(failed),
                    "items": [
                        {
                            "id": (
                                item["manifest"]["id"]
                                if item.get("manifest")
                                else None
                            ),
                            "status": item.get("status"),
                            "error": item.get("error"),
                        }
                        for item in plugins
                    ],
                },
            }
        except Exception as exc:
            return {
                "status": "error",
                "details": {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }

    @staticmethod
    def _check_active_workspace() -> dict[str, Any]:
        try:
            with session_scope() as session:
                setting = SettingsRepository(session).find_one(
                    scope="user",
                    key=ACTIVE_WORKSPACE_KEY,
                )

                if setting is None or not setting.value_json:
                    return {
                        "status": "not_configured",
                        "details": {"workspace_id": None},
                    }

                workspace_id = str(setting.value_json)
                workspace = WorkspaceRepository(session).get(workspace_id)

                if workspace is None:
                    return {
                        "status": "error",
                        "details": {
                            "workspace_id": workspace_id,
                            "reason": "workspace_not_found",
                        },
                    }

                if workspace.status != "active":
                    return {
                        "status": "error",
                        "details": {
                            "workspace_id": workspace_id,
                            "workspace_status": workspace.status,
                        },
                    }

                return {
                    "status": "ok",
                    "details": {
                        "workspace_id": workspace.id,
                        "name": workspace.name,
                        "workspace_type": workspace.workspace_type,
                    },
                }
        except Exception as exc:
            return {
                "status": "error",
                "details": {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
