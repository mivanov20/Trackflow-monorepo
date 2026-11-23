#!/usr/bin/env bash
# TrackFlow server baseline: non-root operational user, no direct root SSH, separated folder permissions, a firewall that exposes only what is needed.
#
#   sudo ./harden-server.sh --ssh-key-file /path/to/operator.pub [--operator alice] [--ssh-port 22] [--dry-run]
#
# Debian/Ubuntu (apt, ufw, systemd). Idempotent: running it twice changes nothing the second time. Read docs/security/server-hardening.md first.
#
# LOCK-OUT GUARD. Root login is switched off only AFTER the deploy user has a working key and `sshd -t` accepts the new configuration; the script refuses
# to continue without --ssh-key-file. Keep your current root session open until you have opened a second session as the new user.

set -euo pipefail

# ---- the baseline, in one place (tests/infra/test_hardening.py reads these lines) -------------------------------------------------------------------
DEPLOY_USER="trackflow"
APP_DIR="/opt/trackflow/app"        # code and compose files: owned by the deploy user, nobody else can write
LOG_DIR="/var/log/trackflow"        # logs: the deploy user writes, the `adm` group reads, others nothing
CONFIG_DIR="/etc/trackflow"         # SECRETS (.env with carrier API keys, JWT secret): root writes, the deploy group reads, others nothing
PUBLIC_TCP_PORTS="80 443"           # HTTPS API + frontend behind the reverse proxy (80 only redirects to 443)
SSH_PORT="22"                       # operations access: key only, rate limited
# NOT public and never opened: 3000/3001 (Next.js), 8000 (API), 6379 (Redis), 6333 (Qdrant), 5555 (Flower), 8765 (MCP): they listen on loopback / the
# Docker network and the reverse proxy is the only way in. Carrier integration credentials therefore cannot be reached from a customer chat path.
# -------------------------------------------------------------------------------------------------------------------------------------------------

OPERATOR=""
KEY_FILE=""
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ssh-key-file) KEY_FILE="$2"; shift 2 ;;
    --operator) OPERATOR="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

run() { if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
say() { echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ -n "$KEY_FILE" ] && [ -r "$KEY_FILE" ] || { echo "--ssh-key-file <public key> is required: without a key for the new user, switching root login off would lock you out" >&2; exit 1; }
grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+|sk-ssh-ed25519@openssh.com) ' "$KEY_FILE" || { echo "$KEY_FILE does not look like a public key" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. the operational user: no password, no sudo, no root. It owns the app and nothing else.
say "deploy user '$DEPLOY_USER'"
id "$DEPLOY_USER" >/dev/null 2>&1 || run adduser --disabled-password --gecos "TrackFlow deploy" "$DEPLOY_USER"
run passwd -l "$DEPLOY_USER" >/dev/null 2>&1 || true
run install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
run install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$KEY_FILE" "/home/$DEPLOY_USER/.ssh/authorized_keys"

# 2. folders: code, logs and secrets are separate, each with the least access that works.
say "folders and permissions"
run install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
run install -d -m 750 -o "$DEPLOY_USER" -g adm "$LOG_DIR"
run install -d -m 750 -o root -g "$DEPLOY_USER" "$CONFIG_DIR"
[ -f "$CONFIG_DIR/trackflow.env" ] || run install -m 640 -o root -g "$DEPLOY_USER" /dev/null "$CONFIG_DIR/trackflow.env"
run chmod 640 "$CONFIG_DIR/trackflow.env"
run chown root:"$DEPLOY_USER" "$CONFIG_DIR/trackflow.env"

# 3. SSH: drop-ins, validated BEFORE they are applied.
say "sshd hardening"
run install -d -m 755 /etc/ssh/sshd_config.d
run install -m 644 "$HERE/sshd_config.d/10-trackflow-hardening.conf" /etc/ssh/sshd_config.d/10-trackflow-hardening.conf
ALLOWED="$DEPLOY_USER${OPERATOR:+ $OPERATOR}"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] write /etc/ssh/sshd_config.d/20-trackflow-allowusers.conf: AllowUsers $ALLOWED / Port $SSH_PORT"
else
  printf 'AllowUsers %s\nPort %s\n' "$ALLOWED" "$SSH_PORT" > /etc/ssh/sshd_config.d/20-trackflow-allowusers.conf
  chmod 644 /etc/ssh/sshd_config.d/20-trackflow-allowusers.conf
  sshd -t   # a broken configuration aborts the script here (set -e), before sshd is touched
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
fi

# 4. firewall: deny everything inbound, then open exactly: ops SSH (rate limited) and HTTPS (+ HTTP for the redirect).
say "firewall (ufw)"
command -v ufw >/dev/null 2>&1 || run apt-get install -y ufw
run ufw --force reset
run ufw default deny incoming
run ufw default allow outgoing
run ufw limit "${SSH_PORT}/tcp" comment 'ops ssh, rate limited'
for port in $PUBLIC_TCP_PORTS; do run ufw allow "${port}/tcp" comment 'public web'; done
run ufw --force enable

say "done. Open a SECOND terminal and sign in as '$DEPLOY_USER' (or '${OPERATOR:-your operator}') with your key BEFORE closing this session, then run verify-server.sh."
