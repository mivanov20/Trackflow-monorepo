"""The server baseline as code: these tests read infra/hardening/* and docker-compose.yml and fail if the baseline drifts from the acceptance criteria
(no direct root SSH login, the firewall exposes only the needed ports, code / logs / secrets are separated). They cannot prove a REAL server is
configured (verify-server.sh does that on the machine); they prove the artifacts that configure it say what they must.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HARDENING = ROOT / "infra" / "hardening"
SCRIPT = (HARDENING / "harden-server.sh").read_text(encoding="utf-8")
VERIFY = (HARDENING / "verify-server.sh").read_text(encoding="utf-8")
SSHD = (HARDENING / "sshd_config.d" / "10-trackflow-hardening.conf").read_text(encoding="utf-8")

NEEDED_PUBLIC = {"80", "443"}  # HTTPS API + frontend behind the reverse proxy (80 only redirects)
INTERNAL_PORTS = {"3000", "3001", "8000", "6379", "6333", "5555", "8765"}


def sshd_directives(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition(" ")
            found.setdefault(key.lower(), value.strip())  # sshd keeps the FIRST value of a keyword
    return found


def variable(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"', SCRIPT, re.M)
    assert match, f"{name} is not defined in harden-server.sh"
    return match.group(1)


# ---------------------------------------------------------------- SSH
def test_root_cannot_sign_in_over_ssh_and_passwords_are_off():
    directives = sshd_directives(SSHD)
    assert directives["permitrootlogin"] == "no"
    assert directives["passwordauthentication"] == "no"
    assert directives["kbdinteractiveauthentication"] == "no"
    assert directives["permitemptypasswords"] == "no"
    assert directives["authenticationmethods"] == "publickey"
    assert int(directives["maxauthtries"]) <= 3
    assert directives["x11forwarding"] == "no" and directives["allowtcpforwarding"] == "no"


def test_the_dropin_wins_over_the_distribution_default():
    # sshd uses the first value it reads and reads sshd_config.d/*.conf in lexical order: the file must sort before any distribution file (50-*, 99-*)
    assert (HARDENING / "sshd_config.d" / "10-trackflow-hardening.conf").name < "50-cloud-init.conf"
    assert "/etc/ssh/sshd_config.d/10-trackflow-hardening.conf" in SCRIPT


def test_only_named_users_may_sign_in_and_root_is_not_one_of_them():
    assert "AllowUsers" in SCRIPT and 'ALLOWED="$DEPLOY_USER' in SCRIPT
    assert "root" not in re.search(r'ALLOWED="([^"]*)"', SCRIPT).group(1)


def test_root_login_is_never_switched_off_without_a_key_for_the_replacement_user():
    refuse = SCRIPT.index("--ssh-key-file <public key> is required")
    assert refuse < SCRIPT.index('install -m 644 "$HERE/sshd_config.d/10-trackflow-hardening.conf"')  # no key file, nothing is changed
    key_installed = SCRIPT.index('/.ssh/authorized_keys"')
    validated = SCRIPT.index("\n  sshd -t")
    applied = SCRIPT.index("  systemctl reload ssh")
    assert key_installed < validated < applied  # key installed, config validated, THEN applied


# ---------------------------------------------------------------- operational user and folders
def test_the_operational_user_is_not_root_and_has_no_password_or_sudo():
    assert variable("DEPLOY_USER") not in ("root", "")
    assert "adduser --disabled-password" in SCRIPT and "passwd -l" in SCRIPT
    assert not re.search(r"usermod[^\n]*-aG[^\n]*(sudo|wheel|admin)", SCRIPT)


def test_code_logs_and_secrets_live_in_separate_folders_with_distinct_permissions():
    app_dir, log_dir, config_dir = variable("APP_DIR"), variable("LOG_DIR"), variable("CONFIG_DIR")
    assert len({app_dir, log_dir, config_dir}) == 3 and not config_dir.startswith(app_dir)  # secrets are not inside the code tree
    assert re.search(r'install -d -m 750 -o "\$DEPLOY_USER" -g "\$DEPLOY_USER" "\$APP_DIR"', SCRIPT)
    assert re.search(r'install -d -m 750 -o "\$DEPLOY_USER" -g adm "\$LOG_DIR"', SCRIPT)
    assert re.search(r'install -d -m 750 -o root -g "\$DEPLOY_USER" "\$CONFIG_DIR"', SCRIPT)
    assert 'chmod 640 "$CONFIG_DIR/trackflow.env"' in SCRIPT  # nobody outside the deploy group reads the secrets, and the deploy user cannot rewrite them
    private = [line for line in SCRIPT.splitlines() if line.lstrip().startswith("run install -d") and re.search(r"APP_DIR|LOG_DIR|CONFIG_DIR|\.ssh", line)]
    assert len(private) == 4
    for line in private:
        assert re.search(r"-m \d\d0 ", line), f"gives 'others' access: {line}"


# ---------------------------------------------------------------- firewall
def test_the_firewall_denies_by_default_and_opens_only_the_needed_ports():
    assert "ufw default deny incoming" in SCRIPT
    public = set(variable("PUBLIC_TCP_PORTS").split())
    assert public == NEEDED_PUBLIC
    assert not (public | {variable("SSH_PORT")}) & INTERNAL_PORTS
    opened = re.findall(r"ufw (?:allow|limit) ", SCRIPT)
    assert len(opened) == 2  # one rule for SSH (limit), one loop over PUBLIC_TCP_PORTS: nothing else is ever opened
    assert 'ufw limit "${SSH_PORT}/tcp"' in SCRIPT  # SSH is rate limited


def test_the_verifier_checks_the_same_ports_as_the_script():
    assert re.search(r'^PUBLIC_PORTS="80 443"', VERIFY, re.M)
    assert "permitrootlogin no" in VERIFY and "passwordauthentication no" in VERIFY and "ONLY the needed ports" in VERIFY


# ---------------------------------------------------------------- the stack itself
def published_ports():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for mapping in service.get("ports", []):
            yield name, str(mapping)


def test_data_services_are_published_on_loopback_only():
    exposed = {name: mapping for name, mapping in published_ports() if name in ("redis", "qdrant", "flower")}
    assert set(exposed) == {"redis", "qdrant", "flower"}
    for name, mapping in exposed.items():
        assert mapping.startswith("127.0.0.1:"), f"{name} is published on every interface: {mapping}"  # no password / API key / login on these


# ---------------------------------------------------------------- the scripts are valid shell
def find_bash():
    """A bash that works (on Windows `bash` may be the WSL launcher with no distribution installed, which is not one)."""
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            try:
                if subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, timeout=20).stdout.strip() == "ok":
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue
    return None


BASH = find_bash()


@pytest.mark.skipif(BASH is None, reason="no working bash")
@pytest.mark.parametrize("script", ["harden-server.sh", "verify-server.sh"])
def test_the_scripts_parse(script):
    result = subprocess.run([BASH, "-n", str(HARDENING / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(BASH is None, reason="no working bash")
def test_the_hardening_script_refuses_to_run_without_a_key_file():
    result = subprocess.run([BASH, str(HARDENING / "harden-server.sh")], capture_output=True, text=True)
    assert result.returncode != 0 and ("run as root" in result.stderr or "--ssh-key-file" in result.stderr)
