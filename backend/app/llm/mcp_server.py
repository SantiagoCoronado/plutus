"""The MCP control plane (spec §13.3): the SAME tool registry, served over
stdio to terminal Claude Code or any MCP client.

Runs on the host against the compose services:
    claude mcp add plutus -- uv --directory ~/plutus/backend run plutus-mcp
Needs DATABASE_URL → localhost:5433 and REDIS_URL → localhost:6379 (the repo
root .env native-dev values); trigger_scan / run_strategy_backtest enqueue
Celery jobs through Redis.

Rules of this surface:
- stdout carries ONLY protocol frames; all logging goes to stderr.
- MCP_TOOL_TIER=read locks it to queries; the hard-coded exclusion list in
  tooldefs applies regardless of tier (those tools simply don't exist).
- Writes execute without confirmation cards (the MCP client is the owner's
  own terminal) but every call is audited with source='mcp' and shows up in
  the Settings "recent agent actions" feed.
- No LLM calls happen here — the MCP client IS the model — so no token budget.
"""

from __future__ import annotations

import json
import logging
import sys

import anyio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


def build_server(tier: str):
    import mcp.types as types
    from mcp.server import Server

    from app.core.db import SessionLocal
    from app.llm.executor import execute_tool
    from app.llm.tooldefs import tools_for_tier

    log = get_logger(__name__)
    server = Server("plutus")
    available = tools_for_tier(tier)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=tool.name, description=tool.description,
                       inputSchema=tool.schema)
            for tool in available
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        session = SessionLocal()
        try:
            outcome = execute_tool(
                session, name, arguments or {},
                source="mcp", allowed_tier=tier, confirm_writes=False,
            )
        finally:
            session.close()
        log.info("mcp_tool_call", tool=name, ok=outcome.ok, tier=outcome.tier)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(outcome.as_tool_result(), default=str),
            )
        ]

    return server


async def _run() -> None:
    from mcp.server.stdio import stdio_server

    settings = get_settings()
    tier = settings.mcp_tool_tier if settings.mcp_tool_tier in ("read", "write") else "write"
    server = build_server(tier)
    log = get_logger(__name__)
    log.info("plutus_mcp_starting", tier=tier)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    configure_logging(level=logging.INFO, stream=sys.stderr)
    anyio.run(_run)


if __name__ == "__main__":
    main()
