"""Thin entry shim so `uv run main.py` keeps working; logic lives in the
songmirror package (also runnable as `python -m songmirror`)."""

from songmirror.cli import main

if __name__ == "__main__":
    main()
