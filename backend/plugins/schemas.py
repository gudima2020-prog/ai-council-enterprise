from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PluginBackendConfig:
    entry: str | None = None
    prefix: str | None = None


@dataclass(frozen=True)
class PluginFrontendConfig:
    entry: str | None = None
    workspace: bool = False
    menu_label: str | None = None


@dataclass(frozen=True)
class PluginEventConfig:
    subscribes: list[str] = field(default_factory=list)
    publishes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    enabled: bool = True
    backend: PluginBackendConfig = field(default_factory=PluginBackendConfig)
    frontend: PluginFrontendConfig = field(default_factory=PluginFrontendConfig)
    permissions: list[str] = field(default_factory=list)
    events: PluginEventConfig = field(default_factory=PluginEventConfig)
    settings_schema: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    platform: dict[str, Any] = field(default_factory=dict)
