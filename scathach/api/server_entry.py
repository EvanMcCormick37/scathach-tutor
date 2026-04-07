"""
Entry point for the scathach API server.

Used as:
  - The `scathach-server` console script (pyproject.toml)
  - The PyInstaller target for the Tauri sidecar binary

Usage:
    scathach-server [--port PORT] [--host HOST]
    python -m scathach.api.server_entry --port 8765
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="scathach FastAPI server")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port to listen on (default: 8765)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only)",
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "scathach.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
