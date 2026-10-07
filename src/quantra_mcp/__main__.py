"""``python -m quantra_mcp`` == console script ``quantra-mcp``."""

from __future__ import annotations

import argparse
import sys

from quantra_mcp import __version__
from quantra_mcp.config import Settings, SettingsError
from quantra_mcp.server import configure_logging, run


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="quantra-mcp",
        description="MCP server for the Quantra pricing engine (stdio by default).",
    )
    parser.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address for --http (default 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8765, help="port for --http (default 8765)")
    parser.add_argument("--version", action="version", version=f"quantra-mcp {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"quantra-mcp: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    run(settings, http=args.http, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
