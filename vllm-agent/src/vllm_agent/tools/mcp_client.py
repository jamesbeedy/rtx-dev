"""MCP client registry for vllm-agent worker.

Loads an `mcpServers` config block (Claude Code / Cursor / Continue compatible),
opens a session per server, and wraps each remote tool as a vllm_agent `Tool`.

Lifecycle:
    reg = MCPRegistry()
    extra = await reg.connect(load_mcp_config(...))
    try:
        # run loop with extra tools merged into the registry
        ...
    finally:
        await reg.aclose()

Sessions are stdio subprocesses (or SSE / streamable-HTTP clients) tied to this
registry instance. Always pair connect() with aclose() in a finally block to
avoid orphaned child processes.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Mapping

from .base import Tool, ToolContext


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: Any, env: Mapping[str, str]) -> Any:
    """Expand ${VAR} / ${VAR:-default} references in `value` using `env`.

    Non-string values pass through unchanged. Unknown vars with no default
    expand to empty string (matching POSIX shell semantics) — easier to
    debug than leaving the literal `${VAR}` in a header.
    """
    if not isinstance(value, str):
        return value
    def _sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        val = env.get(name)
        if val:
            return val
        return default if default is not None else ""
    return _VAR_RE.sub(_sub, value)


def _expand_dict(d: Mapping[str, Any] | None, env: Mapping[str, str]) -> dict[str, str]:
    if not d:
        return {}
    out: dict[str, str] = {}
    for k, v in d.items():
        out[k] = _expand(v, env)
    return out


def _to_openai_schema(
    server: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server}__{name}",
            "description": description or f"MCP tool {name} from server {server}",
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


def load_mcp_config(
    path: str | None = None,
    inline_json: str | None = None,
) -> dict[str, Any]:
    """Resolve MCP config from (in order): explicit inline JSON, explicit path,
    env `VLLM_AGENT_MCP_CONFIG_JSON`, env `VLLM_AGENT_MCP_CONFIG`,
    `~/.config/vllm-agent/mcp.json`. Returns `{}` if none found."""
    if inline_json:
        return json.loads(inline_json)
    if path:
        return json.loads(Path(path).read_text())
    env_inline = os.environ.get("VLLM_AGENT_MCP_CONFIG_JSON")
    if env_inline:
        return json.loads(env_inline)
    env_path = os.environ.get("VLLM_AGENT_MCP_CONFIG")
    if env_path:
        return json.loads(Path(env_path).read_text())
    default = Path("~/.config/vllm-agent/mcp.json").expanduser()
    if default.exists():
        return json.loads(default.read_text())
    return {}


class MCPRegistry:
    """Owns a set of live MCP client sessions and the Tool wrappers around them."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, Any] = {}
        self.tools: dict[str, Tool] = {}
        self.redact_values: list[str] = []

    async def connect(
        self,
        config: dict[str, Any],
        env_source: Mapping[str, str] | None = None,
    ) -> dict[str, Tool]:
        """Connect to all configured MCP servers.

        `env_source` is the secret source used both for `${VAR}` expansion in
        per-server `env` / `headers` values and as the base environment merged
        into stdio subprocesses. Defaults to `os.environ`. Callers (api.py)
        should pass `{**os.environ, **env_overlay}` so worker-side env_overlay
        keys (e.g. `GITHUB_TOKEN`, `MCP_*`) are visible to MCP subprocesses
        without round-tripping through the worker process environment.
        """
        servers = (config or {}).get("mcpServers") or {}
        if not servers:
            return {}

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        base_env: dict[str, str] = {**os.environ}
        if env_source:
            base_env.update(env_source)

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        for server_name, spec in servers.items():
            transport = (spec.get("transport") or "stdio").lower()
            spec_env = _expand_dict(spec.get("env"), base_env)
            spec_headers = _expand_dict(spec.get("headers"), base_env)
            # Redact any non-empty secret-bearing value we resolved. This
            # catches both literal secrets and ${VAR}-substituted values.
            self.redact_values.extend(v for v in spec_env.values() if v)
            self.redact_values.extend(v for v in spec_headers.values() if v)

            if transport == "stdio":
                # Subprocess inherits base_env (host env + env_overlay) plus
                # any per-server overrides. Inheritance covers option 3
                # ("token already on worker, no per-server env block").
                subproc_env = {**base_env, **spec_env}
                params = StdioServerParameters(
                    command=spec["command"],
                    args=list(spec.get("args") or []),
                    env=subproc_env,
                )
                read, write = await self._stack.enter_async_context(
                    stdio_client(params)
                )
            elif transport == "sse":
                from mcp.client.sse import sse_client
                read, write = await self._stack.enter_async_context(
                    sse_client(spec["url"], headers=spec_headers)
                )
            elif transport in ("http", "streamable-http", "streamablehttp"):
                from mcp.client.streamable_http import streamablehttp_client
                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(spec["url"], headers=spec_headers)
                )
            else:
                raise ValueError(
                    f"unknown MCP transport {transport!r} for server {server_name!r}"
                )

            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._sessions[server_name] = session

            listed = await session.list_tools()
            allow = set(spec.get("enabled_tools") or [])
            for t in listed.tools:
                if allow and t.name not in allow:
                    continue
                qual = f"mcp__{server_name}__{t.name}"
                schema = _to_openai_schema(
                    server_name,
                    t.name,
                    getattr(t, "description", "") or "",
                    getattr(t, "inputSchema", None) or {},
                )
                self.tools[qual] = Tool(
                    name=qual,
                    schema=schema,
                    execute=self._make_executor(server_name, t.name),
                )

        return self.tools

    def _make_executor(self, server: str, tool: str):
        async def _exec(
            args: dict[str, Any], ctx: ToolContext
        ) -> dict[str, Any]:
            session = self._sessions.get(server)
            if session is None:
                return {"error": f"mcp server {server!r} not connected"}
            try:
                result = await session.call_tool(tool, arguments=args or {})
            except Exception as e:
                return {"error": f"mcp call failed: {type(e).__name__}: {e}"}

            parts: list[Any] = []
            for c in (getattr(result, "content", None) or []):
                ctype = getattr(c, "type", None)
                if ctype == "text":
                    parts.append(getattr(c, "text", ""))
                elif hasattr(c, "model_dump"):
                    parts.append(c.model_dump())
                else:
                    parts.append(str(c))
            return {
                "is_error": bool(getattr(result, "isError", False)),
                "content": parts,
            }

        return _exec

    async def aclose(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            finally:
                self._stack = None
        self._sessions.clear()
