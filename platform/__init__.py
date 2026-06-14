"""
TALOS platform package.

This package shadows stdlib's `platform` module on the import path. Re-export
stdlib platform attributes so stdlib modules (uuid, pytest internals, etc.) that
do `import platform; platform.system()` keep working.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys


def _load_stdlib_platform():
    """Load stdlib's platform.py directly from the filesystem, bypassing sys.path lookup."""
    for p in sys.path:
        # os.path.join('', 'platform.py') == 'platform.py' (cwd) — safe to check
        candidate = os.path.join(p, "platform.py") if p else "platform.py"
        if os.path.isfile(candidate):
            loader = importlib.machinery.SourceFileLoader("_talos_stdlib_platform", candidate)
            spec = importlib.util.spec_from_loader("_talos_stdlib_platform", loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


_sp = _load_stdlib_platform()
if _sp is not None:
    # Inject all public stdlib platform attributes into this package's namespace
    # so callers that do `import platform; platform.system()` get the real function.
    globals().update({k: getattr(_sp, k) for k in dir(_sp) if not k.startswith("_")})

del _load_stdlib_platform, _sp
