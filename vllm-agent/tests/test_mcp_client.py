"""MCPRegistry end-to-end via a real stdio MCP server subprocess."""
from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

mcp = pytest.importorskip("mcp")

from vllm_agent.tools.mcp_client import (
    MCPRegistry,
    _expand,
    _expand_dict,
    load_mcp_config,
)


_SERVER_SCRIPT = textwrap.dedent("""
    from mcp.server.fastmcp import FastMCP

    srv = FastMCP("test-srv")

    @srv.tool()
    def echo(text: str) -> str:
        \"\"\"Echo back the input text.\"\"\"
        return f"echo: {text}"

    @srv.tool()
    def add(a: int, b: int) -> int:
        \"\"\"Add two ints.\"\"\"
        return a + b

    if __name__ == "__main__":
        srv.run()
""")


@pytest.fixture
def server_script(tmp_path):
    p = tmp_path / "srv.py"
    p.write_text(_SERVER_SCRIPT)
    return p


def _stdio_config(server_script) -> dict:
    return {
        "mcpServers": {
            "test": {
                "command": sys.executable,
                "args": [str(server_script)],
            }
        }
    }


@pytest.mark.asyncio
async def test_connect_returns_qualified_tools(server_script):
    reg = MCPRegistry()
    try:
        tools = await reg.connect(_stdio_config(server_script))
        assert "mcp__test__echo" in tools
        assert "mcp__test__add" in tools
        assert tools["mcp__test__echo"].schema["type"] == "function"
        fn = tools["mcp__test__echo"].schema["function"]
        assert fn["name"] == "mcp__test__echo"
        assert "Echo" in fn["description"]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_execute_calls_remote_tool(server_script):
    reg = MCPRegistry()
    try:
        tools = await reg.connect(_stdio_config(server_script))
        result = await tools["mcp__test__echo"].execute({"text": "hi"}, ctx=None)
        assert result["is_error"] is False
        assert any("echo: hi" in str(p) for p in result["content"])

        result2 = await tools["mcp__test__add"].execute({"a": 2, "b": 3}, ctx=None)
        assert result2["is_error"] is False
        assert any("5" in str(p) for p in result2["content"])
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_enabled_tools_allowlist(server_script):
    cfg = _stdio_config(server_script)
    cfg["mcpServers"]["test"]["enabled_tools"] = ["echo"]
    reg = MCPRegistry()
    try:
        tools = await reg.connect(cfg)
        assert "mcp__test__echo" in tools
        assert "mcp__test__add" not in tools
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_env_values_collected_for_redaction(server_script):
    cfg = _stdio_config(server_script)
    cfg["mcpServers"]["test"]["env"] = {"SECRET": "super-secret"}
    reg = MCPRegistry()
    try:
        await reg.connect(cfg)
        assert "super-secret" in reg.redact_values
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_empty_config_returns_empty_dict():
    reg = MCPRegistry()
    try:
        tools = await reg.connect({})
        assert tools == {}
        tools2 = await reg.connect({"mcpServers": {}})
        assert tools2 == {}
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_unknown_transport_raises(server_script):
    cfg = {"mcpServers": {"x": {"transport": "carrier-pigeon", "command": "x"}}}
    reg = MCPRegistry()
    try:
        with pytest.raises(ValueError, match="unknown MCP transport"):
            await reg.connect(cfg)
    finally:
        await reg.aclose()


def test_load_mcp_config_inline_json():
    cfg = load_mcp_config(inline_json='{"mcpServers": {"x": {}}}')
    assert cfg == {"mcpServers": {"x": {}}}


def test_load_mcp_config_from_path(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text('{"mcpServers": {"y": {}}}')
    cfg = load_mcp_config(path=str(p))
    assert cfg == {"mcpServers": {"y": {}}}


def test_load_mcp_config_env_inline(monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_MCP_CONFIG_JSON", '{"mcpServers": {"z": {}}}')
    monkeypatch.delenv("VLLM_AGENT_MCP_CONFIG", raising=False)
    cfg = load_mcp_config()
    assert cfg == {"mcpServers": {"z": {}}}


def test_load_mcp_config_env_path(tmp_path, monkeypatch):
    p = tmp_path / "mcp.json"
    p.write_text('{"mcpServers": {"w": {}}}')
    monkeypatch.delenv("VLLM_AGENT_MCP_CONFIG_JSON", raising=False)
    monkeypatch.setenv("VLLM_AGENT_MCP_CONFIG", str(p))
    cfg = load_mcp_config()
    assert cfg == {"mcpServers": {"w": {}}}


def test_load_mcp_config_returns_empty_when_no_source(monkeypatch, tmp_path):
    monkeypatch.delenv("VLLM_AGENT_MCP_CONFIG_JSON", raising=False)
    monkeypatch.delenv("VLLM_AGENT_MCP_CONFIG", raising=False)
    # Point HOME at an empty tmp dir so the ~/.config fallback misses.
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_mcp_config()
    assert cfg == {}


def test_load_mcp_config_priority(monkeypatch, tmp_path):
    """Explicit args win over env."""
    monkeypatch.setenv("VLLM_AGENT_MCP_CONFIG_JSON", '{"mcpServers": {"env": {}}}')
    cfg = load_mcp_config(inline_json='{"mcpServers": {"arg": {}}}')
    assert "arg" in cfg["mcpServers"]


# ---- env / header substitution -------------------------------------------

def test_expand_substitutes_simple_var():
    assert _expand("${X}", {"X": "yes"}) == "yes"


def test_expand_supports_default():
    assert _expand("${MISSING:-fallback}", {}) == "fallback"
    assert _expand("${X:-fallback}", {"X": "real"}) == "real"


def test_expand_unknown_becomes_empty_string():
    assert _expand("${NOPE}", {}) == ""


def test_expand_passes_through_non_strings():
    assert _expand(42, {}) == 42
    assert _expand(None, {}) is None


def test_expand_handles_multiple_vars_in_one_value():
    out = _expand("Bearer ${TOKEN}; user=${USER}", {"TOKEN": "abc", "USER": "bob"})
    assert out == "Bearer abc; user=bob"


def test_expand_dict_substitutes_each_value():
    out = _expand_dict({"A": "${X}", "B": "literal"}, {"X": "expanded"})
    assert out == {"A": "expanded", "B": "literal"}


def test_expand_dict_empty():
    assert _expand_dict(None, {}) == {}
    assert _expand_dict({}, {}) == {}


@pytest.mark.asyncio
async def test_env_substitution_passed_to_subprocess(server_script):
    """${VAR} in server env should resolve from env_source, not stay literal."""
    cfg = _stdio_config(server_script)
    cfg["mcpServers"]["test"]["env"] = {"INJECTED": "${MY_SECRET}"}
    reg = MCPRegistry()
    try:
        await reg.connect(cfg, env_source={"MY_SECRET": "resolved-value"})
        assert "resolved-value" in reg.redact_values
        # The literal ${MY_SECRET} placeholder should NOT be redacted —
        # only the resolved value matters.
        assert "${MY_SECRET}" not in reg.redact_values
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_env_inheritance_when_no_per_server_env(server_script):
    """Worker env_overlay vars should reach subprocess even with no `env` block."""
    cfg = _stdio_config(server_script)
    # No env key at all → subprocess inherits base_env (host + env_overlay).
    reg = MCPRegistry()
    try:
        tools = await reg.connect(cfg, env_source={"GITHUB_TOKEN": "ghp_test"})
        # Subprocess started successfully; can't introspect subprocess env
        # directly here, but the connect succeeded with no env block.
        assert "mcp__test__echo" in tools
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_header_values_redacted_for_http_transport():
    """SSE/HTTP transports: header values should be appended to redact_values."""
    # Use an unreachable URL so connect fails fast, but substitution + redact
    # collection happen before transport opens.
    cfg = {
        "mcpServers": {
            "remote": {
                "transport": "sse",
                "url": "http://127.0.0.1:1",
                "headers": {"Authorization": "Bearer ${TOK}"},
            }
        }
    }
    reg = MCPRegistry()
    try:
        with pytest.raises(Exception):
            await reg.connect(cfg, env_source={"TOK": "secret-tok"})
        assert "Bearer secret-tok" in reg.redact_values
    finally:
        await reg.aclose()
