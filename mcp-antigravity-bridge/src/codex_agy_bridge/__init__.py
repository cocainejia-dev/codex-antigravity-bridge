"""codex_agy_bridge - let Codex call Google Antigravity via MCP."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

try:
    __version__ = package_version("codex-agy-bridge")
except PackageNotFoundError:
    # Source-tree imports remain supported without inventing a release version.
    __version__ = "0+unknown"

from .server import mcp

__all__ = ["mcp", "__version__"]
