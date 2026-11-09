"""Exercise the native FTRACE writer and its bounded capture contract."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('probe', ['concurrency.py', 'contract.py'])
def test_ftrace_native_contract(probe):
    if not (os.environ.get('I2_CLANG_CL') or shutil.which('clang-cl')):
        pytest.skip('clang-cl unavailable')
    result = subprocess.run([sys.executable, str(ROOT / 'tests/native/ftrace' / probe)],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
