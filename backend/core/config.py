from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT_DIR / ".env"
CONFIG_PATH = ROOT_DIR / "config" / "config.json"
USER_SETTINGS_PATH = ROOT_DIR / "data" / "user_settings.json"


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppSettings:
    app_name: str
    app_version: str
    environment: str
    debug: bool
    host: str
    port: int
    openrouter_api_key: str
    default_provider: str
    default_model: str
    max_tokens: int
    temperature: float
    request_timeout_seconds: int

    @property
    def has_openrouter_key(self) -> bool:
        return bool(self.openrouter_api_key)


class ConfigurationManager:
    DEFAULTS: dict[str, Any] = {
        "app_name": "AI Studio Enterprise",
        "app_version": "0.14.0",
        "environment": "development",
        "debug": True,
        "host": "127.0.0.1",
        "port": 8000,
        "default_provider": "openrouter",
        "default_model": "openrouter/free",
        "max_tokens": 1000,
        "temperature": 0.4,
        "request_timeout_seconds": 60
    }

    ENV_MAP = {
        "AI_STUDIO_APP_NAME": "app_name",
        "AI_STUDIO_APP_VERSION": "app_version",
        "AI_STUDIO_ENVIRONMENT": "environment",
        "AI_STUDIO_DEBUG": "debug",
        "AI_STUDIO_HOST": "host",
        "AI_STUDIO_PORT": "port",
        "AI_STUDIO_DEFAULT_PROVIDER": "default_provider",
        "OPENROUTER_DEFAULT_MODEL": "default_model",
        "AI_STUDIO_DEFAULT_MODEL": "default_model",
        "AI_STUDIO_MAX_TOKENS": "max_tokens",
        "AI_STUDIO_TEMPERATURE": "temperature",
        "AI_STUDIO_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds"
    }

    def __init__(
        self,
        env_path: Path = ENV_PATH,
        config_path: Path = CONFIG_PATH,
        user_settings_path: Path = USER_SETTINGS_PATH
    ) -> None:
        self.env_path = env_path
        self.config_path = config_path
        self.user_settings_path = user_settings_path

    def load(self) -> AppSettings:
        load_dotenv(self.env_path)
        merged = dict(self.DEFAULTS)
        merged.update(self._load_json(self.config_path))
        merged.update(self._load_json(self.user_settings_path))
        merged.update(self._load_environment())

        settings = AppSettings(
            app_name=str(merged["app_name"]).strip(),
            app_version=str(merged["app_version"]).strip(),
            environment=str(merged["environment"]).strip(),
            debug=self._to_bool(merged["debug"]),
            host=str(merged["host"]).strip(),
            port=self._to_int(merged["port"], "port"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            default_provider=str(merged["default_provider"]).strip(),
            default_model=str(merged["default_model"]).strip(),
            max_tokens=self._to_int(merged["max_tokens"], "max_tokens"),
            temperature=self._to_float(merged["temperature"], "temperature"),
            request_timeout_seconds=self._to_int(
                merged["request_timeout_seconds"],
                "request_timeout_seconds"
            )
        )
        self._validate(settings)
        return settings

    def _load_environment(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for env_name, setting_name in self.ENV_MAP.items():
            value = os.getenv(env_name)
            if value not in (None, ""):
                result[setting_name] = value
        return result

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON file: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"Configuration must be an object: {path}")
        return data

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ConfigurationError(f"Invalid boolean value: {value!r}")

    @staticmethod
    def _to_int(value: Any, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{name} must be an integer") from exc

    @staticmethod
    def _to_float(value: Any, name: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{name} must be numeric") from exc

    @staticmethod
    def _validate(settings: AppSettings) -> None:
        if not 1 <= settings.port <= 65535:
            raise ConfigurationError("port must be between 1 and 65535")
        if settings.max_tokens < 1:
            raise ConfigurationError("max_tokens must be positive")
        if not 0.0 <= settings.temperature <= 2.0:
            raise ConfigurationError("temperature must be between 0.0 and 2.0")
        if settings.request_timeout_seconds < 1:
            raise ConfigurationError("request_timeout_seconds must be positive")


def get_settings() -> AppSettings:
    return ConfigurationManager().load()
