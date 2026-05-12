"""build_system_prompt MCP-tool indexing."""
from __future__ import annotations

from vllm_agent.prompts import _format_mcp_block, build_system_prompt
from vllm_agent.tools.base import Tool


def _tool(name: str, desc: str) -> Tool:
    return Tool(
        name=name,
        schema={"type": "function",
                "function": {"name": name, "description": desc,
                             "parameters": {"type": "object"}}},
        execute=lambda *a, **k: None,
    )


def test_empty_mcp_block_returns_empty_string():
    assert _format_mcp_block({}) == ""
    assert _format_mcp_block(None) == ""


def test_mcp_block_lists_tools():
    tools = {
        "mcp__fs__read": _tool("mcp__fs__read", "Read a file"),
        "mcp__gh__list_issues": _tool("mcp__gh__list_issues", "List GH issues"),
    }
    out = _format_mcp_block(tools)
    assert "Additional MCP tools available" in out
    assert "mcp__fs__read: Read a file" in out
    assert "mcp__gh__list_issues: List GH issues" in out


def test_mcp_block_truncates_long_descriptions():
    long_desc = "x" * 500
    tools = {"mcp__big__t": _tool("mcp__big__t", long_desc)}
    out = _format_mcp_block(tools)
    assert "..." in out
    assert "x" * 500 not in out


def test_mcp_block_caps_total_size():
    tools = {
        f"mcp__s__t{i}": _tool(f"mcp__s__t{i}", f"desc {i}")
        for i in range(500)
    }
    out = _format_mcp_block(tools, max_chars=400)
    assert len(out) < 600
    assert "truncated" in out


def test_mcp_block_handles_missing_description():
    tools = {"mcp__x__y": _tool("mcp__x__y", "")}
    out = _format_mcp_block(tools)
    assert "mcp__x__y" in out
    # No trailing colon-space when desc is empty.
    assert "mcp__x__y:" not in out


def test_build_system_prompt_includes_mcp_index():
    tools = {"mcp__fs__read": _tool("mcp__fs__read", "Read a file")}
    out = build_system_prompt(
        skill_content=None, workdir="/tmp/x", mode="local", mcp_tools=tools,
    )
    assert "mcp__fs__read: Read a file" in out


def test_build_system_prompt_omits_block_when_no_mcp():
    out = build_system_prompt(
        skill_content=None, workdir="/tmp/x", mode="local", mcp_tools=None,
    )
    assert "Additional MCP tools" not in out
