#!/usr/bin/env bash
# Phase 10: install the already-configured GitHub Actions runner as a
# systemd service so it survives logout/reboot. Does not download the
# runner, does not call config.sh, and does not accept a token — that is
# the Human registration step on the production EC2.
#
#   sudo ./scripts/install_runner_service.sh /path/to/actions-runner
#
# The directory must already contain GitHub's svc.sh and a completed
# `./config.sh --url ... --token ...`. Do not run this on the home VM.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "re-run as root: sudo $0 /path/to/actions-runner" >&2
  exit 1
fi

if [[ "${#}" -ne 1 ]]; then
  echo "usage: sudo $0 /path/to/actions-runner" >&2
  exit 1
fi

REAL_USER=${SUDO_USER:-${USER}}
if [[ "${REAL_USER}" == "root" ]]; then
  echo "run this with sudo from your login user, not as a root shell, so the runner service is not owned by root" >&2
  exit 1
fi

if [[ ! -d "$1" ]]; then
  echo "not a directory: $1" >&2
  exit 1
fi

RUNNER_DIR=$(cd "$1" && pwd)

if [[ ! -f "${RUNNER_DIR}/svc.sh" ]]; then
  echo "no svc.sh in ${RUNNER_DIR}; download the runner tarball and run ./config.sh first" >&2
  exit 1
fi

if [[ ! -f "${RUNNER_DIR}/.runner" ]]; then
  echo "${RUNNER_DIR} is not configured; run ./config.sh with the GitHub registration token first" >&2
  exit 1
fi

cd "${RUNNER_DIR}"
bash ./svc.sh install "${REAL_USER}"
bash ./svc.sh start

echo
echo "runner service installed from ${RUNNER_DIR}"
echo "status: sudo bash ${RUNNER_DIR}/svc.sh status"
echo "confirm Idle: GitHub → repo Settings → Actions → Runners"
