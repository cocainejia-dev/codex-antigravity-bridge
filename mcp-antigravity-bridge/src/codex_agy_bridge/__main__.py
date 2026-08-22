"""Entry point: `python -m codex_agy_bridge` or `codex-agy-bridge`."""

from __future__ import annotations

import sys

from .server import mcp


def main(argv: list[str] | None = None) -> int | None:
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in ("doctor", "--doctor"):
        from .doctor import main as doctor_main

        return doctor_main(argv[1:])

    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
