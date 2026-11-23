# Server hardening baseline

What a TrackFlow server must look like before it takes traffic, and how to prove it. Everything is code in [`infra/hardening/`](../../infra/hardening): a script that applies the baseline, a script that verifies it, and tests that fail if either drifts from the acceptance criteria. (The repo has no access to a real server: the scripts and tests are written and checked here, and have to be **run once on the target machine** — the checklist at the end is what to paste into the delivery.)

## The baseline

| Area | Rule | Where it is enforced |
|---|---|---|
| Operational user | A dedicated non-root user `trackflow`: no password (`adduser --disabled-password`, `passwd -l`), no `sudo`/`wheel`/`admin` group, key-only login. It owns the application and nothing else. | `harden-server.sh` §1, `verify-server.sh` |
| SSH | **No direct root login** (`PermitRootLogin no`), keys only (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `AuthenticationMethods publickey`), `AllowUsers` limited to the deploy user and named operators, 3 tries, 20 s grace, no forwarding/tunnels. Installed as a drop-in `10-trackflow-hardening.conf` so the distribution file is untouched and sshd (which keeps the first value it reads) uses ours. | `sshd_config.d/10-trackflow-hardening.conf`, `harden-server.sh` §3 |
| Folders | Code, logs and secrets are separate trees with different owners and modes (below). | `harden-server.sh` §2 |
| Firewall | `ufw`: deny all incoming, allow all outgoing; open **22/tcp (rate-limited `ufw limit`)**, **443/tcp** (HTTPS API + frontend) and **80/tcp** (only to redirect to 443). Nothing else. | `harden-server.sh` §4 |
| Stack ports | Next.js (3000/3001), API (8000), Redis (6379), Qdrant (6333), Flower (5555) and the MCP server (8765) are never public: they listen on loopback or the Docker network and a reverse proxy is the only way in. In `docker-compose.yml` Redis, Qdrant and Flower are published on `127.0.0.1` only (audit finding C-02). | `docker-compose.yml`, `tests/infra/test_hardening.py` |

### Folder permissions

| Path | Holds | Owner:group | Mode | Why |
|---|---|---|---|---|
| `/opt/trackflow/app` | code, compose files | `trackflow:trackflow` | `750` | only the deploy user changes code; others cannot read it |
| `/var/log/trackflow` | application and proxy logs | `trackflow:adm` | `750` | the app writes, the `adm` group (operators) reads, nobody else |
| `/etc/trackflow` | **sensitive configuration** | `root:trackflow` | `750` | secrets are outside the code tree; only root writes |
| `/etc/trackflow/trackflow.env` | `JWT_SECRET`, `LLM_API_KEY`, `RESEND_API_KEY`, `AGENT_OAUTH_CLIENT_SECRET`, carrier credentials | `root:trackflow` | `640` | the service can read it, cannot rewrite it, others see nothing; never in git, never in an image |

The carrier-integration credentials in that file are used by backend processes only. A customer chat path cannot reach them: the support agent has no carrier tool, the process that runs it does not export them to the model, and no internal port is reachable from outside.

### Reverse proxy (not in this repo's compose, required in front of it)

Terminate TLS 1.2+ on 443, redirect 80 → 443, forward `X-Forwarded-Proto` (the API then sends `Strict-Transport-Security`) and `X-Forwarded-For`, set `TRUSTED_PROXY=true` for the API so sign-in throttling counts the real client address, add the browser headers for the Next.js apps (the API already sets its own: `services/hardening.py`), rate-limit `/auth/*` and `/telemetry/events`, and set `APP_ENV=production` (switches the interactive API docs off).

## Apply it

```bash
# 1. from your workstation: copy your PUBLIC key and this folder to the server (never a private key, never a password)
scp -r infra/hardening ~/.ssh/id_ed25519.pub admin@server:/tmp/
# 2. on the server, in the session you are already logged into (keep it open)
sudo bash /tmp/hardening/harden-server.sh --ssh-key-file /tmp/id_ed25519.pub --operator alice
# 3. from a SECOND terminal, prove the new user works BEFORE closing the first
ssh trackflow@server
# 4. verify
sudo bash /tmp/hardening/verify-server.sh
```

Safety design: the script refuses to run without a public key; it installs the key for the new user, validates the new SSH configuration with `sshd -t`, and only then reloads sshd and enables the firewall. `--dry-run` prints every action without changing anything. Running it twice changes nothing.

## Prove it (acceptance criteria)

| Criterion | Evidence to capture on the server |
|---|---|
| No direct root SSH login | `sudo sshd -T \| grep -E 'permitrootlogin\|passwordauthentication'` → `permitrootlogin no`, `passwordauthentication no`; `ssh root@<server>` → `Permission denied (publickey)` |
| Firewall exposes only the needed ports | `sudo ufw status verbose` → default deny incoming; only 22 (LIMIT), 80, 443; `ss -ltn` shows nothing else on a public address; from outside, `nmap -p 22,80,443,3000,3001,5555,6333,6379,8000 <server>` shows only 22/80/443 open |
| Non-root operational user, folder separation | `id trackflow`; `stat -c '%a %U:%G %n' /opt/trackflow/app /var/log/trackflow /etc/trackflow /etc/trackflow/trackflow.env` |
| All of the above in one command | `sudo bash verify-server.sh` → `ALL CHECKS PASSED` (exit code 0) |

What is already proven **in this repository** (no server needed): `python -m pytest tests/infra -q` — 12 checks that the SSH drop-in says `PermitRootLogin no`/key-only, that the script will not touch SSH without a key, that the deploy user has no sudo, that code, logs and secrets are three separate trees with no access for "others", that the firewall opens exactly SSH + 80/443 and none of the internal ports, that Redis/Qdrant/Flower are loopback-only in compose, and that both scripts parse as valid shell.

## Limits, honestly

- The scripts target Debian/Ubuntu with `ufw` and systemd and **have not been run against a real server** from this environment; `bash -n` and the static tests are what ran. Run `verify-server.sh` on the machine and attach its output.
- `ufw` does not filter traffic that Docker publishes itself (Docker writes its own iptables rules). That is why the compose file binds the data services to `127.0.0.1` rather than relying on the firewall for them; keep every published port in `docker-compose.yml` on `127.0.0.1` except what the reverse proxy needs.
- TLS certificates and the reverse proxy configuration are the deployer's: not included here.
