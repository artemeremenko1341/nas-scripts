"""Tiny .env loader. Imports load .env into os.environ if not already set.

Usage in any script:
    import sys; sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
    import _env  # noqa: F401  (side-effect: loads .env)
"""
import os
from pathlib import Path

_ENV_PATH = Path('/volume1/homes/artemere-7601341/scripts/.env')


def load_env(path: Path = _ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_env()
