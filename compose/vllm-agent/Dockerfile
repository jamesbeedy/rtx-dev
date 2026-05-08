# Tiny vllm-agent runtime image: python:3.12-slim + an agent user with home dir.
# Source is bind-mounted from the host at /app; pip install -e runs at container
# start (compose's `command:`).
#
# Container starts as root so the entrypoint can chown the named-volume mount
# points (Docker mounts named volumes as root regardless of USER), then drops
# to the agent user via gosu before running the supplied CMD.

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 -s /bin/bash agent \
 && mkdir -p /home/agent/.cache/vllm-agent/runs \
              /home/agent/.cache/vllm-agent/workspaces \
              /var/lib/vllm-agent/sessions \
 && chown -R agent:agent /home/agent /var/lib/vllm-agent

WORKDIR /app

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
