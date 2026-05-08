name: __PROFILE_NAME__
description: GPU passthrough VM running vLLM (model __VLLM_MODEL__)

config:
  limits.cpu: "__LIMITS_CPU__"
  limits.memory: __LIMITS_MEMORY__
  migration.stateful: "false"
  boot.mode: uefi-nosecureboot
  cloud-init.user-data: |
    #cloud-config
    package_update: true
    package_upgrade: false
    packages:
      - python3-venv
      - python3-pip
      - wget
      - curl
      - jq

    write_files:
      - path: /etc/systemd/system/vllm.service
        permissions: '0644'
        content: |
          [Unit]
          Description=vLLM OpenAI-compatible inference server (__VLLM_MODEL__)
          After=network-online.target
          Wants=network-online.target
          StartLimitIntervalSec=120
          StartLimitBurst=5

          [Service]
          Type=simple
          User=root
          Environment=HOME=/root
          Environment=HF_HOME=/root/.cache/huggingface
          ExecStart=/opt/vllm/bin/vllm serve __VLLM_MODEL__ \
            --host 0.0.0.0 --port 8000 \
            --gpu-memory-utilization __VLLM_GPU_UTIL__ \
            --max-model-len __VLLM_MAX_LEN__ \
            --quantization __VLLM_QUANT____VLLM_API_KEY_ARG__
          Restart=on-failure
          RestartSec=10
          TimeoutStartSec=900
          TimeoutStopSec=60
          LimitNOFILE=1048576
          KillMode=mixed

          [Install]
          WantedBy=multi-user.target

      - path: /etc/profile.d/cuda.sh
        permissions: '0755'
        content: |
          export PATH="/usr/local/cuda/bin:${PATH}"
          export LD_LIBRARY_PATH="/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"

    runcmd:
      - cd /tmp && wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
      - dpkg -i /tmp/cuda-keyring_1.1-1_all.deb
      - apt-get update -qq
      - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-open
      - python3 -m venv /opt/vllm
      - /opt/vllm/bin/pip install --upgrade -q pip wheel
      - /opt/vllm/bin/pip install -q vllm
      - systemctl daemon-reload
      - systemctl enable vllm.service

      # NOTE: vllm-agent is installed by launch-inference.sh post-VM-up via
      # `lxc file push` of the local repo tarball (avoids needing GitHub auth
      # in the VM for private repos). The systemd unit below is enabled but
      # not started here — launch-inference.sh starts it after the install.

      # Install systemd unit for vllm-agent serve
      - |
        cat > /etc/systemd/system/vllm-agent.service <<'EOF'
        [Unit]
        Description=vllm-agent HTTP server (agent runtime backed by local vLLM)
        After=network-online.target vllm.service
        Wants=network-online.target
        StartLimitIntervalSec=120
        StartLimitBurst=5

        [Service]
        Type=simple
        User=ubuntu
        Group=ubuntu
        WorkingDirectory=/home/ubuntu/rtx_5090_dev/vllm-agent
        Environment=VLLM_BASE_URL=http://127.0.0.1:8000
        Environment=VLLM_MODEL=__VLLM_MODEL__
        Environment="DDG_MIN_INTERVAL_S=__DDG_MIN_INTERVAL__"
        Environment="VLLM_AGENT_API_KEY=__VLLM_AGENT_API_KEY__"
        ExecStart=/home/ubuntu/rtx_5090_dev/vllm-agent/.venv-agent/bin/vllm-agent serve --host 0.0.0.0 --port 8088
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
        EOF
      - systemctl daemon-reload
      - systemctl enable vllm-agent.service

    power_state:
      mode: reboot
      delay: now
      condition: True
      message: Rebooting to load NVIDIA open kernel modules and start vllm.service

devices:
  eth0:
    name: eth0
    nictype: bridged
    parent: __BRIDGE__
    type: nic
  root:
    path: /
    pool: __STORAGE_POOL__
    size: __ROOT_SIZE__
    type: disk
