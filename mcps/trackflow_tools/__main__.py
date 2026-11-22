"""Runs the TrackFlow MCP server:  python -m mcps.trackflow_tools   (settings from the environment, see config.py).

Exit codes: 0 normal stop; 2 configuration error (a required setting is missing or invalid); 3 the OAuth provider's metadata could not be fetched.
"""

import logging
import sys

import uvicorn

from . import errors
from .app import create_app, load_auth_server
from .config import ConfigError, load_settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return errors.EXIT_CONFIG_ERROR
    try:
        auth_server = load_auth_server(settings)
    except Exception as error:  # noqa: BLE001 - without the provider's keys no token can be validated: do not start
        print(f"error: could not read the OAuth provider's metadata from {settings.issuer} ({type(error).__name__}). Is the issuer URL right and the provider running?", file=sys.stderr)
        return 3
    uvicorn.run(create_app(settings, auth_server=auth_server), host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
