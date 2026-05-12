# Tiny vllm-agent runtime image: python:3.12-slim + an agent user with home dir.
# Source is bind-mounted from the host at /app; pip install -e runs at container
# start (compose's `command:`).
#
# Container starts as root so the entrypoint can chown the named-volume mount
# points (Docker mounts named volumes as root regardless of USER), then drops
# to the agent user via gosu before running the supplied CMD.

FROM python:3.12-slim

# `uv` / `uvx` binaries from the official Astral image — used to launch
# Python-based MCP servers configured via `command: "uvx"`.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Base packages plus git, the GitHub CLI, and Node.js (for `npx`-launched
# MCP servers). Worker MCP configs routinely reference `command: "npx"`
# (Node MCPs) or `command: "uvx"` (Python MCPs), so both must be on PATH
# inside the container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        gosu git ca-certificates curl gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
 && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get install -y --no-install-recommends gh nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 -s /bin/bash agent \
 && mkdir -p /home/agent/.cache/vllm-agent/runs \
              /home/agent/.cache/vllm-agent/workspaces \
              /home/agent/.cache/uv \
              /home/agent/.npm \
              /var/lib/vllm-agent/sessions \
 && chown -R agent:agent /home/agent /var/lib/vllm-agent

WORKDIR /app

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin \
    UV_CACHE_DIR=/home/agent/.cache/uv \
    NPM_CONFIG_CACHE=/home/agent/.npm

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
