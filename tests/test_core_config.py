from pathlib import Path
import json

import pytest

from backend.core.config import ConfigurationError, ConfigurationManager


def test_loads_project_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"app_name": "Test Studio", "port": 9000}),
        encoding="utf-8"
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    settings = ConfigurationManager(
        env_path=tmp_path / ".env",
        config_path=config_path,
        user_settings_path=tmp_path / "missing.json"
    ).load()

    assert settings.app_name == "Test Studio"
    assert settings.port == 9000
    assert settings.has_openrouter_key is True


def test_rejects_invalid_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_STUDIO_PORT", "99999")
    with pytest.raises(ConfigurationError):
        ConfigurationManager(
            env_path=tmp_path / ".env",
            config_path=tmp_path / "missing.json",
            user_settings_path=tmp_path / "missing-user.json"
        ).load()
