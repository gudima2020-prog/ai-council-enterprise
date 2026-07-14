from __future__ import annotations

from dataclasses import asdict
from importlib import import_module, invalidate_caches
from pathlib import Path
from types import ModuleType
from typing import Any
import json
import re
import sys

from fastapi import APIRouter, FastAPI

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.plugins.schemas import (
    PluginBackendConfig,
    PluginEventConfig,
    PluginFrontendConfig,
    PluginManifest,
)


PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")


class PluginValidationError(RuntimeError):
    pass


class PluginLoadError(RuntimeError):
    pass


class PluginLoader:
    def __init__(
        self,
        *,
        plugins_dir: Path,
        event_bus: EventBus,
    ) -> None:
        self.plugins_dir = plugins_dir
        self.event_bus = event_bus
        self.logger = LoggerManager.get_logger("plugins")
        self._manifests: dict[str, PluginManifest] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._routers: dict[str, APIRouter] = {}

    async def discover(self) -> list[PluginManifest]:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        manifests: list[PluginManifest] = []

        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue

            manifest_path = plugin_dir / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = self._load_manifest(manifest_path)
                self._validate_package_structure(plugin_dir, manifest)

                self._manifests[manifest.id] = manifest
                self._status[manifest.id] = {
                    "status": "discovered",
                    "error": None,
                    "path": str(plugin_dir),
                }
                manifests.append(manifest)

                await self.event_bus.publish(
                    Event(
                        event_type="plugin.discovered",
                        source="plugin_loader",
                        payload={
                            "plugin_id": manifest.id,
                            "version": manifest.version,
                        },
                    )
                )
            except Exception as exc:
                self.logger.exception(
                    "Plugin discovery failed path=%s",
                    plugin_dir,
                )
                self._status[plugin_dir.name] = {
                    "status": "failed",
                    "error": str(exc),
                    "path": str(plugin_dir),
                }

        return manifests

    async def load_all(self) -> None:
        if not self._manifests:
            await self.discover()

        self._ensure_plugins_import_path()

        for manifest in list(self._manifests.values()):
            if not manifest.enabled:
                self._status[manifest.id]["status"] = "disabled"
                continue

            try:
                router = self._load_backend_router(manifest)

                if router is not None:
                    self._routers[manifest.id] = router

                self._status[manifest.id]["status"] = "loaded"
                self._status[manifest.id]["error"] = None

                await self.event_bus.publish(
                    Event(
                        event_type="plugin.loaded",
                        source="plugin_loader",
                        payload={
                            "plugin_id": manifest.id,
                            "version": manifest.version,
                        },
                    )
                )
            except Exception as exc:
                self._status[manifest.id]["status"] = "failed"
                self._status[manifest.id]["error"] = str(exc)

                self.logger.exception(
                    "Plugin load failed plugin_id=%s",
                    manifest.id,
                )

                await self.event_bus.publish(
                    Event(
                        event_type="plugin.failed",
                        source="plugin_loader",
                        payload={
                            "plugin_id": manifest.id,
                            "error": exc.__class__.__name__,
                        },
                    )
                )

    def register_routers(self, app: FastAPI) -> None:
        for plugin_id, router in self._routers.items():
            manifest = self._manifests[plugin_id]
            prefix = manifest.backend.prefix or f"/api/plugins/{plugin_id}"

            app.include_router(
                router,
                prefix=prefix,
                tags=[f"plugin:{plugin_id}"],
            )

            self._status[plugin_id]["status"] = "running"

    def list_plugins(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for plugin_id, manifest in self._manifests.items():
            status = self._status.get(plugin_id, {})
            result.append(
                {
                    "manifest": asdict(manifest),
                    "status": status.get("status", "unknown"),
                    "error": status.get("error"),
                    "path": status.get("path"),
                }
            )

        for plugin_id, status in self._status.items():
            if plugin_id in self._manifests:
                continue

            result.append(
                {
                    "manifest": None,
                    "status": status.get("status", "failed"),
                    "error": status.get("error"),
                    "path": status.get("path"),
                }
            )

        return result

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        manifest = self._manifests.get(plugin_id)

        if manifest is None:
            return None

        status = self._status.get(plugin_id, {})

        return {
            "manifest": asdict(manifest),
            "status": status.get("status", "unknown"),
            "error": status.get("error"),
            "path": status.get("path"),
        }

    def _load_manifest(self, path: Path) -> PluginManifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginValidationError(
                f"Invalid JSON manifest: {path}"
            ) from exc

        plugin_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()

        if not plugin_id:
            raise PluginValidationError("Plugin id is required.")

        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise PluginValidationError(
                "Plugin id may contain only a-z, 0-9, '_' and '-'."
            )

        if not name:
            raise PluginValidationError("Plugin name is required.")

        if not version:
            raise PluginValidationError("Plugin version is required.")

        backend_raw = raw.get("backend") or {}
        frontend_raw = raw.get("frontend") or {}
        events_raw = raw.get("events") or {}

        manifest = PluginManifest(
            id=plugin_id,
            name=name,
            version=version,
            description=str(raw.get("description", "")),
            author=str(raw.get("author", "")),
            enabled=bool(raw.get("enabled", True)),
            backend=PluginBackendConfig(
                entry=backend_raw.get("entry"),
                prefix=backend_raw.get("prefix"),
            ),
            frontend=PluginFrontendConfig(
                entry=frontend_raw.get("entry"),
                workspace=bool(frontend_raw.get("workspace", False)),
                menu_label=frontend_raw.get("menu_label"),
            ),
            permissions=list(raw.get("permissions", [])),
            events=PluginEventConfig(
                subscribes=list(events_raw.get("subscribes", [])),
                publishes=list(events_raw.get("publishes", [])),
            ),
            settings_schema=dict(raw.get("settings_schema", {})),
            dependencies=list(raw.get("dependencies", [])),
            platform=dict(raw.get("platform", {})),
        )

        self._validate_backend_entry(manifest)
        return manifest

    def _validate_backend_entry(self, manifest: PluginManifest) -> None:
        entry = manifest.backend.entry

        if not entry:
            return

        if ":" not in entry:
            raise PluginValidationError(
                f"Invalid backend entry for plugin {manifest.id}: {entry}"
            )

        module_path, attribute_name = entry.split(":", 1)

        if not module_path.startswith(f"{manifest.id}."):
            raise PluginValidationError(
                "Plugin backend entry must use its own package namespace. "
                f"Expected prefix '{manifest.id}.', got '{module_path}'."
            )

        if not attribute_name:
            raise PluginValidationError(
                f"Missing backend attribute in entry: {entry}"
            )

    def _validate_package_structure(
        self,
        plugin_dir: Path,
        manifest: PluginManifest,
    ) -> None:
        if plugin_dir.name != manifest.id:
            raise PluginValidationError(
                "Plugin directory name must match plugin id. "
                f"Directory='{plugin_dir.name}', id='{manifest.id}'."
            )

        if not manifest.backend.entry:
            return

        package_init = plugin_dir / "__init__.py"
        if not package_init.exists():
            raise PluginValidationError(
                f"Plugin package is missing __init__.py: {package_init}"
            )

    def _ensure_plugins_import_path(self) -> None:
        plugins_path = str(self.plugins_dir.resolve())

        if plugins_path not in sys.path:
            sys.path.insert(0, plugins_path)

        invalidate_caches()

    def _load_backend_router(
        self,
        manifest: PluginManifest,
    ) -> APIRouter | None:
        entry = manifest.backend.entry

        if not entry:
            return None

        module_path, attribute_name = entry.split(":", 1)

        try:
            module: ModuleType = import_module(module_path)
        except ModuleNotFoundError as exc:
            raise PluginLoadError(
                f"Cannot import plugin module '{module_path}'. "
                f"Check package structure and plugin.json."
            ) from exc

        value = getattr(module, attribute_name, None)

        if not isinstance(value, APIRouter):
            raise PluginLoadError(
                f"Backend entry must resolve to APIRouter: {entry}"
            )

        return value
