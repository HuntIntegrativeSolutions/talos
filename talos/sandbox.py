"""
TALOS Docker FS sandbox (P3c, ADR-010).

Two-level containment:
  [TALOS worker process]     — has network (Postgres, NEXUS MCP, model APIs)
      └── [Docker subprocess] — network:none, readOnlyRoot
           └── [agent-generated code runs here]

The Docker subprocess is invoked ONLY for untrusted agent-generated code execution.
The worker process itself is not sandboxed at the network level.

TALOS_SANDBOX_MODE:
  docker (default) — run code inside Docker container with full isolation flags
  none             — run code in-process (UNSAFE); emits CRITICAL warning on startup
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_BYPASS_WARNING = (
    "TALOS_SANDBOX_MODE=none: code-exec sandbox is disabled. "
    "This is a security risk. Do not use in production."
)

_DOCKERFILE_DIR = Path(__file__).resolve().parent / "sandbox"
_IMAGE_NAME = "talos-sandbox"

_DOCKER_RUN_FLAGS = [
    "--network", "none",
    "--read-only",
    "--tmpfs", "/tmp:size=64m",
    "--memory", "256m",
    "--cpus", "0.5",
    "--rm",
    "--user", "nobody",
    "--security-opt", "no-new-privileges",
]


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


class Sandbox:
    def __init__(self):
        self.mode = os.environ.get("TALOS_SANDBOX_MODE", "docker")

        if self.mode == "none":
            log.critical(_BYPASS_WARNING)
            bypass_log = Path("./talos-sandbox-bypass.log")
            bypass_log.write_text(_BYPASS_WARNING + "\n", encoding="utf-8")

    def run(self, code: str, timeout_s: int = 30) -> SandboxResult:
        if self.mode == "none":
            return self._run_inprocess(code, timeout_s)
        return self._run_docker(code, timeout_s)

    def _run_docker(self, code: str, timeout_s: int) -> SandboxResult:
        cmd = [
            "docker", "run",
            *_DOCKER_RUN_FLAGS,
            _IMAGE_NAME,
            "python", "-c", code,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return SandboxResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
                exit_code=-1,
                timed_out=True,
            )

    def _run_inprocess(self, code: str, timeout_s: int) -> SandboxResult:
        import io
        import contextlib
        import signal

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        exit_code = 0
        timed_out = False

        def _timeout_handler(signum, frame):
            raise TimeoutError("sandbox timeout")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_s)
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, "<sandbox>", "exec"), {})  # noqa: S102
        except TimeoutError:
            timed_out = True
            exit_code = -1
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:
            stderr_buf.write(f"{type(exc).__name__}: {exc}\n")
            exit_code = 1
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        return SandboxResult(
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            exit_code=exit_code,
            timed_out=timed_out,
        )
