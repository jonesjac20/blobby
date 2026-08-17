#!/usr/bin/env bash
# Phase 7 VM bootstrap. Run from anywhere; repo is the parent of this script.
#
#   sudo ./scripts/vm_bootstrap.sh
#
# Installs Python 3.13 or 3.12, a repo-local venv, runtime deps only
# (requirements.txt — not pytest), opens 8000/tcp in ufw, and installs a
# systemd unit so the game survives an SSH disconnect. Does not enable ufw
# if it is inactive (that can lock you out of SSH). Does not touch the
# existing 2222 → VM:22 forward.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "re-run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
REAL_USER=${SUDO_USER:-${USER}}
if [[ "${REAL_USER}" == "root" ]]; then
  echo "run this with sudo from your login user, not as a root shell, so the venv is not owned by root" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl gnupg software-properties-common

PYTHON_BIN=""
if command -v python3.13 >/dev/null 2>&1; then
  PYTHON_BIN=python3.13
elif apt-get install -y python3.13 python3.13-venv python3.13-dev && command -v python3.13 >/dev/null 2>&1; then
  PYTHON_BIN=python3.13
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN=python3.12
elif apt-get install -y python3.12 python3.12-venv python3.12-dev && command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN=python3.12
else
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y python3.13 python3.13-venv python3.13-dev
  PYTHON_BIN=python3.13
fi

VENV="${REPO_ROOT}/.venv"
sudo -u "${REAL_USER}" "${PYTHON_BIN}" -m venv "${VENV}"
sudo -u "${REAL_USER}" "${VENV}/bin/pip" install --upgrade pip
sudo -u "${REAL_USER}" "${VENV}/bin/pip" install -r "${REPO_ROOT}/requirements.txt"

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH
  ufw allow 22/tcp
  ufw allow 8000/tcp
  echo "ufw rules added. status:"
  ufw status || true
else
  echo "ufw is not installed; skip firewall rule. Install with: apt-get install -y ufw && ufw allow 8000/tcp"
fi

UNIT=/etc/systemd/system/blobby.service
cat > "${UNIT}" <<EOF
[Unit]
Description=blobby game server
After=network.target

[Service]
Type=simple
User=${REAL_USER}
WorkingDirectory=${REPO_ROOT}
ExecStart=${VENV}/bin/python -m server.main
Restart=on-failure
RestartSec=2
Environment=BLOBBY_HOST=0.0.0.0
Environment=BLOBBY_PORT=8000

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable blobby.service

echo
echo "bootstrap ok. python=$("${VENV}/bin/python" --version) repo=${REPO_ROOT}"
echo "start:  sudo systemctl start blobby"
echo "logs:   journalctl -u blobby -f"
echo "LAN IP: $(hostname -I 2>/dev/null | awk '{print $1}')"
echo "then from another machine:  http://<that-ip>:8000"
echo "bots:   python -m bots.simple_bot --url http://<host>:8000/ws --count 5"
