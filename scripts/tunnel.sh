#!/usr/bin/env bash
# Phase 7 CGNAT / no-router-forward fallback.
# The game process must already be listening on 127.0.0.1:8000
# (systemctl start blobby, or python -m server.main).
#
#   ./scripts/tunnel.sh
#
# Prints a https://*.trycloudflare.com URL. Open that in a browser on any
# network; the client upgrades to wss:// on the same host. Bots:
#
#   python -m bots.simple_bot --url https://<subdomain>.trycloudflare.com/ws --count 5
#
# Quick tunnels need no Cloudflare account. They are not a stable URL
# (deferred: DDNS / TLS). Ctrl+C stops the tunnel; the game keeps running.

set -euo pipefail

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "installing cloudflared..."
  ARCH=$(uname -m)
  case "${ARCH}" in
    x86_64|amd64) DEB_ARCH=amd64 ;;
    aarch64|arm64) DEB_ARCH=arm64 ;;
    *)
      echo "unsupported arch ${ARCH}; install cloudflared from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
      exit 1
      ;;
  esac
  TMP=$(mktemp)
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${DEB_ARCH}.deb" -o "${TMP}"
  sudo dpkg -i "${TMP}"
  rm -f "${TMP}"
fi

echo "proxying http://127.0.0.1:8000 — copy the https URL from the log below"
exec cloudflared tunnel --url http://127.0.0.1:8000
