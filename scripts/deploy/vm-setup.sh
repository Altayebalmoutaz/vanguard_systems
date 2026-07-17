#!/usr/bin/env bash
# Idempotent Ubuntu LTS prep for Vanguard production on a GCP Compute Engine VM.
#
# Usage (as a sudo-capable user, NOT as root for the final docker group step):
#   curl -fsSL ... | bash   # or, after cloning the repo:
#   sudo bash scripts/deploy/vm-setup.sh
#
# Safe to re-run. Does not clone the app or start containers — see
# docs/deployment/README.md Phase 10 for deploy commands.
#
# Purpose / expected output / recovery for each step: docs/deployment/README.md § Phase 4.

set -euo pipefail

APP_DIR="${VANGUARD_APP_DIR:-/opt/vanguard}"
DEPLOY_USER="${SUDO_USER:-${USER}}"
TIMEZONE="${VANGUARD_TIMEZONE:-America/New_York}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run with sudo: sudo bash scripts/deploy/vm-setup.sh" >&2
  exit 1
fi

echo "==> [1/10] System update"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

echo "==> [2/10] Base packages (git, curl, ca-certificates, ufw, fail2ban, unattended-upgrades)"
apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  git \
  ufw \
  fail2ban \
  unattended-upgrades \
  apt-listchanges \
  jq \
  htop

echo "==> [3/10] Timezone → ${TIMEZONE}"
timedatectl set-timezone "${TIMEZONE}" || true
timedatectl status || true

echo "==> [4/10] Docker Engine + Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  ARCH="$(dpkg --print-architecture)"
  CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
  echo \
    "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "    docker already installed: $(docker --version)"
fi

systemctl enable --now docker

echo "==> [5/10] Docker group for deploy user: ${DEPLOY_USER}"
if id "${DEPLOY_USER}" >/dev/null 2>&1; then
  usermod -aG docker "${DEPLOY_USER}"
  echo "    added ${DEPLOY_USER} to docker (re-login required for group to take effect)"
else
  echo "    WARN: user ${DEPLOY_USER} not found; skip usermod"
fi

echo "==> [6/10] UFW — allow 22/80/443 only"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
# --force avoids interactive prompt on re-run
ufw --force enable
ufw status verbose

echo "==> [7/10] Fail2Ban (SSH jail)"
systemctl enable --now fail2ban
systemctl status fail2ban --no-pager || true

echo "==> [8/10] Unattended security upgrades"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo "==> [9/10] App directory ${APP_DIR}"
mkdir -p "${APP_DIR}"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "${APP_DIR}"

echo "==> [10/10] Ensure swap exists (headroom for spaCy lg + Next build on small VMs)"
SWAP_SIZE="${VANGUARD_SWAP_SIZE:-2G}"
if swapon --show | grep -q .; then
  echo "    swap already active:"
  swapon --show
elif [[ -e /swapfile ]]; then
  echo "    /swapfile present but inactive — enabling"
  swapon /swapfile
else
  echo "    creating ${SWAP_SIZE} /swapfile"
  # fallocate is fastest; dd is the portable fallback if the filesystem rejects it.
  fallocate -l "${SWAP_SIZE}" /swapfile 2>/dev/null \
    || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -qE '^\s*/swapfile\s' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "    added /swapfile to /etc/fstab (persists across reboots)"
  fi
fi

echo ""
echo "VM prep complete."
echo "Next:"
echo "  1. Re-login (or newgrp docker) so docker works without sudo"
echo "  2. Clone / pull the repo into ${APP_DIR}"
echo "  3. Follow docs/deployment/README.md Phase 7 (DNS) → Phase 10 (deploy)"
echo "  4. GCP VPC firewall: allow tcp:22,80,443 to this VM; deny other public ingress"
