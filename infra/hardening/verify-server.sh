#!/usr/bin/env bash
# Checks the baseline of harden-server.sh on the running server and prints PASS/FAIL per rule. Exit code 1 if any rule fails. Run as root.
#
#   sudo ./verify-server.sh [--public-ports "80 443"] [--ssh-port 22]
#
# Acceptance evidence: (1) `sshd -T` says permitrootlogin no and passwordauthentication no, (2) a real attempt to ssh as root is refused,
# (3) ufw is active with default deny and only the listed ports, (4) nothing but those ports listens on a public address, (5) the permissions are as designed.

set -uo pipefail
PUBLIC_PORTS="80 443"
SSH_PORT="22"
DEPLOY_USER="trackflow"
while [ $# -gt 0 ]; do
  case "$1" in
    --public-ports) PUBLIC_PORTS="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
FAILED=0
check() { # check "<rule>" <command...>
  local rule="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "PASS  $rule"; else echo "FAIL  $rule"; FAILED=1; fi
}
mode_is() { [ "$(stat -c '%a %U:%G' "$1")" = "$2" ]; }

SSHD="$(sshd -T 2>/dev/null)"
check "root cannot sign in over SSH (permitrootlogin no)" grep -qx 'permitrootlogin no' <<<"$SSHD"
check "passwords are not accepted over SSH" grep -qx 'passwordauthentication no' <<<"$SSHD"
check "only public keys are accepted (authenticationmethods publickey)" grep -qx 'authenticationmethods publickey' <<<"$SSHD"
check "SSH is limited to named users (allowusers)" grep -q '^allowusers ' <<<"$SSHD"
check "an attempt to log in as root is refused" bash -c "! ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no -p $SSH_PORT root@127.0.0.1 true"
check "the deploy user exists, is not root and has no sudo" bash -c "id $DEPLOY_USER && [ \"\$(id -u $DEPLOY_USER)\" -ne 0 ] && ! id -nG $DEPLOY_USER | grep -qwE 'sudo|wheel|admin'"

UFW="$(ufw status verbose 2>/dev/null)"
check "firewall is active" grep -q '^Status: active' <<<"$UFW"
check "firewall default is deny incoming" grep -q 'deny (incoming)' <<<"$UFW"
for port in $PUBLIC_PORTS $SSH_PORT; do check "port $port/tcp is allowed" grep -Eq "^$port/tcp +(ALLOW|LIMIT) IN" <<<"$UFW"; done
OPEN="$(ufw status | awk '/^[0-9]+\/tcp +(ALLOW|LIMIT)/ {split($1,a,"/"); print a[1]}' | sort -u | tr '\n' ' ')"
EXPECTED="$(echo "$PUBLIC_PORTS $SSH_PORT" | tr ' ' '\n' | sort -u | tr '\n' ' ')"
check "ONLY the needed ports are open (have: $OPEN / want: $EXPECTED)" [ "$OPEN" = "$EXPECTED" ]

LISTENING="$(ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Ev '^(127\.|\[::1\])' | sed 's/.*://' | sort -un | tr '\n' ' ')"
for port in $LISTENING; do
  case " $PUBLIC_PORTS $SSH_PORT " in *" $port "*) ;; *) echo "FAIL  port $port listens on a public address and is not in the baseline"; FAILED=1 ;; esac
done

check "/opt/trackflow/app is 750 trackflow:trackflow" mode_is /opt/trackflow/app "750 trackflow:trackflow"
check "/var/log/trackflow is 750 trackflow:adm" mode_is /var/log/trackflow "750 trackflow:adm"
check "/etc/trackflow is 750 root:trackflow" mode_is /etc/trackflow "750 root:trackflow"
check "/etc/trackflow/trackflow.env is 640 root:trackflow" mode_is /etc/trackflow/trackflow.env "640 root:trackflow"

[ "$FAILED" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$FAILED"
