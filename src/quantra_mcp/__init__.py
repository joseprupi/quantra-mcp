"""quantra-mcp: an MCP server that fronts the Quantra pricing engine's JSON API.

The server never computes a number. It builds explicit engine requests,
forwards them over HTTP, and returns the engine's response together with the
exact request it sent.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quantra-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
