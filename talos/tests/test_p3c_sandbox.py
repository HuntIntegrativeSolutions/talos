"""
TALOS P3c tests — Docker FS sandbox.

Docker tests are skipped when Docker is unavailable or TALOS_SKIP_DOCKER=1.
The bypass-mode warning test does not require Docker.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REQUIRES_DOCKER = pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("TALOS_SKIP_DOCKER") == "1",
    reason="Docker not available — skipping sandbox tests",
)


# ---------------------------------------------------------------------------
# Test 1: hello world
# ---------------------------------------------------------------------------

@REQUIRES_DOCKER
def test_sandbox_runs_hello_world():
    from talos.sandbox import Sandbox
    sb = Sandbox()
    result = sb.run('print("hello")')
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert not result.timed_out


# ---------------------------------------------------------------------------
# Test 2: network blocked
# ---------------------------------------------------------------------------

@REQUIRES_DOCKER
def test_sandbox_network_blocked():
    from talos.sandbox import Sandbox
    sb = Sandbox()
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://1.1.1.1', timeout=3)\n"
        "    print('CONNECTED')\n"
        "except Exception as e:\n"
        "    print('BLOCKED')\n"
    )
    result = sb.run(code, timeout_s=10)
    assert result.exit_code == 0
    assert "CONNECTED" not in result.stdout, "Network should be blocked inside sandbox"


# ---------------------------------------------------------------------------
# Test 3: read-only filesystem
# ---------------------------------------------------------------------------

@REQUIRES_DOCKER
def test_sandbox_readonly_fs():
    from talos.sandbox import Sandbox
    sb = Sandbox()
    code = (
        "try:\n"
        "    open('/etc/pwned', 'w').write('hacked')\n"
        "    print('WROTE')\n"
        "except Exception:\n"
        "    print('READONLY')\n"
    )
    result = sb.run(code, timeout_s=10)
    assert "WROTE" not in result.stdout, "Filesystem should be read-only inside sandbox"


# ---------------------------------------------------------------------------
# Test 4: timeout
# ---------------------------------------------------------------------------

@REQUIRES_DOCKER
def test_sandbox_timeout():
    from talos.sandbox import Sandbox
    sb = Sandbox()
    result = sb.run("import time; time.sleep(60)", timeout_s=2)
    assert result.timed_out, "Expected timed_out=True"


# ---------------------------------------------------------------------------
# Test 5: bypass mode logs warning (no Docker needed)
# ---------------------------------------------------------------------------

def test_sandbox_bypass_mode_logs_warning(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.setenv("TALOS_SANDBOX_MODE", "none")
    monkeypatch.chdir(tmp_path)

    from talos.sandbox import Sandbox, _BYPASS_WARNING
    import importlib
    import talos.sandbox as sandbox_mod
    importlib.reload(sandbox_mod)

    with caplog.at_level(logging.CRITICAL, logger="talos.sandbox"):
        sb = sandbox_mod.Sandbox()

    bypass_log = tmp_path / "talos-sandbox-bypass.log"
    assert bypass_log.exists(), "bypass log file should be created"
    content = bypass_log.read_text()
    assert _BYPASS_WARNING in content, f"Expected warning in log file; got: {content!r}"
