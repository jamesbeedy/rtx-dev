# Provisioning

## Launch

```bash
./launch-inference.sh --lxd-host USER@LXD-CLUSTER-MEMBER
```

This script:

1. Creates an LXD VM with GPU passthrough.
2. Cloud-init installs Docker CE + nvidia-container-toolkit + the NVIDIA
   open kernel module, then reboots.
3. Tar/scp/lxc-file-push the local repo into the VM at
   `/home/ubuntu/rtx_5090_dev`.
4. Generates `/home/ubuntu/rtx_5090_dev/.env` (mode 0600) from CLI args.
5. Runs `docker compose pull && docker compose up -d` as the ubuntu user.
6. Polls `/v1/models` and `/agent/skills` through nginx until both
   backends are responsive.
7. Updates `.mcp.json` with `VLLM_BASE_URL=http://<VM>:8443` and
   `VLLM_AGENT_URL=http://<VM>:8443/agent`.

After it returns, restart Claude Code (or reload the MCP server) and the tools
will be available.
