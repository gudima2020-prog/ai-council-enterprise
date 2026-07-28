from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_app_container_import_has_no_control_center_cycle() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from backend.core.container import AppContainer; "
                "assert AppContainer.__name__ == 'AppContainer'"
            ),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
