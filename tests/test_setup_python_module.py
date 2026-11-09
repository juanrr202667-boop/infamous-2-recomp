"""Regression tests for setup's PowerShell environment checks."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_missing_python_module_is_a_false_check_not_a_fatal_error():
    """A new virtual environment must reach dependency installation."""
    python = str(Path(sys.executable).resolve()).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        ". './scripts/lib/common.ps1'; "
        f"$found = Test-I2PythonModule '{python}' 'i2_missing_setup_probe_xyz'; "
        "if ($found) { exit 11 }; "
        "Write-Output 'continued'"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "continued"


def test_installed_python_module_is_detected():
    """The check must still accept a module available to Python."""
    python = str(Path(sys.executable).resolve()).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        ". './scripts/lib/common.ps1'; "
        f"if (-not (Test-I2PythonModule '{python}' 'sys')) {{ exit 11 }}; "
        "Write-Output 'found'"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "found"
