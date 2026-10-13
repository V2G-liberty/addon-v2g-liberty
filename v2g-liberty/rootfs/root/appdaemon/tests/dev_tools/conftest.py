"""Make the AppDaemon apps dir importable so ``dev_tools.*`` resolves in pytest,
exactly as AppDaemon does at runtime (it adds apps/ to the path)."""

import sys
from pathlib import Path

_APPS = Path(__file__).resolve().parents[2] / "apps"
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))
