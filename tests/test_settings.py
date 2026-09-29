import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.core.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_app_does_not_start_without_database_url(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "database_url" in (result.stderr + result.stdout).lower()
