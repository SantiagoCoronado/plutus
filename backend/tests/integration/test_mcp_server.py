"""Phase 6 integration: the plutus-mcp stdio server as a real subprocess —
handshake, tier-scoped tool listing, a live tool call against the test db,
and the stdout-is-protocol-only discipline."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.llm.tooldefs import TOOLS
from app.models import AgentToolCall
from tests.integration.conftest import TEST_DB_URL, TEST_REDIS_URL

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]


class McpClient:
    """Minimal JSON-RPC-over-stdio client — enough for a smoke test."""

    def __init__(self, env: dict):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "app.llm.mcp_server"],
            cwd=BACKEND_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        frame = {"jsonrpc": "2.0", "id": self._id, "method": method,
                 "params": params or {}}
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, f"no response to {method}; stderr: {self.proc.stderr.read()[:500]}"
        payload = json.loads(line)  # a non-JSON line here = stdout pollution
        assert payload.get("id") == self._id
        assert "error" not in payload, payload
        return payload["result"]

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def handshake(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        self.notify("notifications/initialized")
        return result

    def close(self) -> str:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        return self.proc.stderr.read()


@pytest.fixture
def mcp_env():
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": TEST_DB_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "APP_AUTH_TOKEN": "mcp-smoke",
    })
    return env


def spawn(env: dict, tier: str) -> McpClient:
    env = {**env, "MCP_TOOL_TIER": tier}
    client = McpClient(env)
    result = client.handshake()
    assert result["serverInfo"]["name"] == "plutus"
    return client


class TestMcpServer:
    def test_write_tier_lists_all_tools_and_calls_one(self, mcp_env):
        seed_assets()
        client = spawn(mcp_env, "write")
        try:
            tools = client.request("tools/list")["tools"]
            assert {tool["name"] for tool in tools} == set(TOOLS)

            result = client.request("tools/call", {
                "name": "search_assets", "arguments": {"query": "AAPL"},
            })
            body = json.loads(result["content"][0]["text"])
            assert body["status"] == "ok"
            assert body["result"]["results"][0]["symbol"] == "AAPL"
        finally:
            stderr = client.close()
        assert "plutus_mcp_starting" in stderr  # logs went to stderr, not stdout

        # the call was audited with source=mcp
        with session_scope() as session:
            row = session.scalar(select(AgentToolCall))
            assert row.source == "mcp" and row.name == "search_assets"

    def test_read_tier_hides_write_tools(self, mcp_env):
        client = spawn(mcp_env, "read")
        try:
            tools = client.request("tools/list")["tools"]
            names = {tool["name"] for tool in tools}
            assert names == {t.name for t in TOOLS.values() if t.tier == "read"}
            assert "add_transaction" not in names
        finally:
            client.close()
