# Tiny vllm-agent runtime image: python:3.12-slim + an agent user with home dir.
# Source is bind-mounted from the host at /app; pip install -e runs at container
# start (compose's `command:`).

FROM python:3.12-slim

# Create agent user matching uid 1000 (the host's ubuntu user)
RUN useradd -m -u 1000 -s /bin/bash agent

USER agent
WORKDIR /app

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin
