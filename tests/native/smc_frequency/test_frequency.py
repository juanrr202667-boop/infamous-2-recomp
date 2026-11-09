import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXE = ROOT / 'build/smc-frequency-fixture/microstep_probe.exe'

def run(mode, enabled):
    env = os.environ.copy()
    env['I2_SMC_FREQUENCY'] = '1' if enabled else '0'
    env['I2_SMC_FREQUENCY_EVERY'] = '1'
    result = subprocess.run([str(EXE), mode], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stderr

def test_counts_each_observed_fallback_when_enabled():
    for mode in ('stqx', 'lqx'):
        text = run(mode, True)
        assert '[i2-smc-frequency]' in text and f'op={mode}' in text

def test_default_off_keeps_output_free_of_frequency_data():
    assert '[i2-smc-frequency]' not in run('stqx', False)
