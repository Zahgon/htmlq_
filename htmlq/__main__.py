"""`python -m htmlq` -- the same entry point as the `htmlq` console script."""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
