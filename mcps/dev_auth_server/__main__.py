"""Runs the development authorization server:  python -m mcps.dev_auth_server  (needs DEV_IDP_CLIENTS, see app.py)."""

import os
import sys

import uvicorn

from .app import create_app, load_clients


def main() -> int:
    issuer = os.getenv("DEV_IDP_ISSUER", "http://localhost:9000")
    try:
        clients = load_clients()
    except Exception as error:  # noqa: BLE001 - a bad registration stops the server, it never starts half configured
        print(f"error: DEV_IDP_CLIENTS is not valid: {error}", file=sys.stderr)
        return 2
    if not clients:
        print("error: DEV_IDP_CLIENTS is empty: register at least one client (see mcps/dev_auth_server/app.py).", file=sys.stderr)
        return 2
    port = int(os.getenv("DEV_IDP_PORT", "9000"))
    uvicorn.run(create_app(issuer, clients=clients), host=os.getenv("DEV_IDP_HOST", "127.0.0.1"), port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
