name: __PROFILE_NAME__
description: GPU passthrough profile for the rtx5090 LXD VM

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
      - ca-certificates
      - curl
      - gnupg
      - jq
      - python3-venv
    runcmd:
      # ---- 1. Docker CE from the official repo --------------------------
      - install -m 0755 -d /etc/apt/keyrings
      - curl -fsSL https://download.docker.com/linux/ubuntu/gpg
          | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      - chmod a+r /etc/apt/keyrings/docker.gpg
      - |
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
          > /etc/apt/sources.list.d/docker.list
      - apt-get update -qq
      - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq
          docker-ce docker-ce-cli containerd.io
          docker-buildx-plugin docker-compose-plugin

      # ---- 2. NVIDIA open kernel module + container toolkit ------------
      - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-open
      - curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey
          | gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit.gpg
      - |
        curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
          | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#' \
          > /etc/apt/sources.list.d/nvidia-container-toolkit.list
      - apt-get update -qq
      - DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-container-toolkit
      - nvidia-ctk runtime configure --runtime=docker
      - systemctl restart docker

      # ---- 3. Allow ubuntu user to invoke docker (no sudo) -------------
      - usermod -aG docker ubuntu

    power_state:
      mode: reboot
      delay: now
      condition: True
      message: Rebooting to load NVIDIA open kernel modules

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
