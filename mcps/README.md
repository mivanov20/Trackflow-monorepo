# `mcps` folder

This folder contains **Model Context Protocol (MCP) Servers** that expose tools, resources, and context to AI models and agents.

Each subfolder inside `mcps/` must correspond to **one specific MCP server** (for example: `database-mcp`, `github-mcp`) and include its own documentation.

- **Main purpose**: to centralize the servers that bridge the gap between AI models and the company's internal systems or data sources.
- **Recommendation**: document the tools and resources exposed by each MCP server.

> _Spanish version: [README.es.md](./README.es.md)._

## Servers in this repository

- [`trackflow_tools`](trackflow_tools/README.md): TrackFlow company tools (tickets of the Incidents Manager, read-only inventory) over Streamable HTTP, protected by OAuth with MCP Auth. Run its tests with `python -m pytest tests/mcps`.
- [`dev_auth_server`](dev_auth_server/app.py): a minimal OAuth 2.0 / OIDC authorization server for development and tests only, so the whole flow runs without an account with a real provider.
