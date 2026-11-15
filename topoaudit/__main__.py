"""Module entry point: ``python -m topoaudit``."""
from __future__ import annotations
from .cli import main
if __name__ == "__main__":
    raise SystemExit(main())
