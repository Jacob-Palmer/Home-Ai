#!/usr/bin/env bash
set -euo pipefail
# Provision and harden Ubuntu on OCI (run as root)

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo ./provision_oci_harden.sh"
  exit 1
fi

PROJECT_USER=aiuser
PROJECT_HOME=/home/${PROJECT_USER}

echo "[1/8] Updating apt and installing base packages..."
apt update && apt upgrade -y
apt install -y curl ca-certificates gnupg lsb-release git ufw fail2ban unattended-upgrades software-properties-common

echo "[2/8] Creating project user '${PROJECT_USER}' (if missing)..."
if ! id -u ${PROJECT_USER} >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" ${PROJECT_USER}
  usermod -aG sudo ${PROJECT_USER}
  mkdir -p ${PROJECT_HOME}/.ssh
  chmod 700 ${PROJECT_HOME}/.ssh
  echo "# add your public key to ${PROJECT_HOME}/.ssh/authorized_keys" > ${PROJECT_HOME}/.ssh/authorized_keys
  chmod 600 ${PROJECT_HOME}/.ssh/authorized_keys
  chown -R ${PROJECT_USER}:${PROJECT_USER} ${PROJECT_HOME}/.ssh
fi

echo "[3/8] SSH hardening..."
SSH_CONFIG=/etc/ssh/sshd_config
cp ${SSH_CONFIG} ${SSH_CONFIG}.bak
sed -i "s/^#*PermitRootLogin.*/PermitRootLogin no/" ${SSH_CONFIG}
sed -i "s/^#*PasswordAuthentication.*/PasswordAuthentication no/" ${SSH_CONFIG}
sed -i "s/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/" ${SSH_CONFIG}
sed -i "s/^#*UsePAM.*/UsePAM yes/" ${SSH_CONFIG}
systemctl reload sshd || true

echo "[4/8] Firewall (UFW) configuration..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8000/tcp    # optional app port for local testing
ufw --force enable

echo "[5/8] Install Python tooling..."
apt install -y python3 python3-venv python3-pip
pip3 install --upgrade pip

echo "[6/8] Unattended upgrades and fail2ban..."
dpkg-reconfigure -f noninteractive unattended-upgrades || true
systemctl enable --now fail2ban || true

echo "[7/8] Optional: create project directory and basic virtualenv"
PROJECT_DIR=/opt/home-ai
mkdir -p ${PROJECT_DIR}
chown ${PROJECT_USER}:${PROJECT_USER} ${PROJECT_DIR}
sudo -u ${PROJECT_USER} python3 -m venv ${PROJECT_DIR}/venv

echo "[8/8] Systemd unit template for FastAPI (example at /etc/systemd/system/gatekeeper.service)"
cat > /etc/systemd/system/gatekeeper.service <<'SERVICE'
[Unit]
Description=Home AI Gatekeeper FastAPI
After=network.target

[Service]
Type=simple
User=aiuser
WorkingDirectory=/opt/home-ai
ExecStart=/opt/home-ai/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
echo "Provisioning script completed. Review /etc/ssh/sshd_config and /etc/systemd/system/gatekeeper.service before starting the service."

exit 0
