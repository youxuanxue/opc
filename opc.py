"""Convenient CLI launcher: python opc.py ..."""

from opc_platform.entrypoints.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

