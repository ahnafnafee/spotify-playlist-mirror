"""Thin entry shim so `uv run main.py` keeps working; logic lives in the
omni_sync package (also runnable as `python -m omni_sync`)."""

from omni_sync.cli import main

if __name__ == "__main__":
    main()
