"""Installed CLI smoke tests against the migrated PostgreSQL 16 service."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine


@pytest.mark.parametrize(
    ("command", "expected_output"),
    (
        ("db-health", "healthy: PostgreSQL 16 reachable"),
        ("db-current", "current_revision=0004"),
    ),
)
def test_installed_database_commands(
    command: str,
    expected_output: str,
    db_engine: Engine,
) -> None:
    del db_engine  # The fixture proves connectivity and owns cleanup.
    executable = shutil.which("bisfin")
    if executable is None:
        executable_name = "bisfin.exe" if os.name == "nt" else "bisfin"
        installed_script = Path(sys.executable).resolve().parent / executable_name
        executable = str(installed_script) if installed_script.is_file() else None
    assert executable is not None

    completed = subprocess.run(
        [executable, command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert expected_output in completed.stdout
    assert "password" not in completed.stdout.lower()
