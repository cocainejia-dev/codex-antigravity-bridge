"""Entry point: `python -m codex_agy_bridge` or `codex-agy-bridge`."""

from .server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()