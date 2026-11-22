"""A small MCP client to try the server by hand: lists the tools and runs one complete flow per tool.

    $env:MCP_TOKEN = "<access token with the scopes you want to try>"
    python -m mcps.trackflow_tools.smoke_client [http://localhost:8765/mcp]

The token needs `tickets:read tickets:write inventory:read` to run every step; with fewer scopes the steps that need the missing ones report
`[INSUFFICIENT_SCOPE]`, which is also the behaviour to see. It creates ONE real ticket (and moves it to in_progress) in the Incidents Manager.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def show(label: str, result) -> None:
    text = result.content[0].text if result.content else ""
    print(f"  {label}: {'ERROR ' if result.isError else 'ok    '}{text[:160]}")


async def main(url: str, token: str) -> int:
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected to {init.serverInfo.name}")
            tools = (await session.list_tools()).tools
            print("tools:", ", ".join(f"{t.name} ({'read' if t.annotations and t.annotations.readOnlyHint else 'write'})" for t in tools))
            print("flows:")
            created = await session.call_tool("create_ticket", {"title": "Smoke test", "description": "Created by smoke_client.", "category": "other", "origin": "internal", "branch": "central"})
            show("create_ticket", created)
            ticket_id = (created.structuredContent or {}).get("id", 1)
            show("get_ticket", await session.call_tool("get_ticket", {"ticket_id": ticket_id}))
            show("update_ticket_status open -> in_progress", await session.call_tool("update_ticket_status", {"ticket_id": ticket_id, "status": "in_progress"}))
            show("update_ticket_status in_progress -> open (not allowed)", await session.call_tool("update_ticket_status", {"ticket_id": ticket_id, "status": "open"}))
            show("query_inventory list", await session.call_tool("query_inventory", {"operation": "list"}))
            show("query_inventory WRITE attempt (must fail)", await session.call_tool("query_inventory", {"operation": "adjust"}))
    return 0


if __name__ == "__main__":
    token = os.getenv("MCP_TOKEN")
    if not token:
        print("error: set MCP_TOKEN to an access token for this server (see the module docstring).", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/mcp", token)))
