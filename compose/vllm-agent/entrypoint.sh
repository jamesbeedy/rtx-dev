#!/bin/sh
# Container starts as root only to fix ownership on named-volume mount points
# (Docker mounts named volumes as root regardless of the image's USER), then
# drops to the agent user via gosu and exec's the supplied CMD.
set -eu

chown -R agent:agent /home/agent/.cache /var/lib/vllm-agent

exec /usr/sbin/gosu agent "$@"
