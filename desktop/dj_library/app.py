"""Command-line entry point for ZotiBox."""
from .application import main
from .ui import Window

if __name__ == "__main__":
    raise SystemExit(main())
