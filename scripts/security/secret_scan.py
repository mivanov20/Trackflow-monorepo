"""Looks for credentials committed to the repository. Exit code 1 if it finds any.

    python scripts/security/secret_scan.py            # every file git tracks
    python scripts/security/secret_scan.py --all      # also untracked files that are not ignored

Two kinds of finding:
  * a KNOWN SECRET SHAPE anywhere (private key blocks, cloud and provider key prefixes, a JWT, a URL with a password inside, a bearer token literal);
  * a literal VALUE assigned to a name that says it is a secret (`api_key = "..."`, `PASSWORD: "..."`) in code, compose or config files.

Not findings: empty values, `${VARIABLE}` references, `os.getenv(...)`, placeholders (`changeme`, `<...>`, `your-...`), and the test and fixture folders, which hold made-up values on purpose
(each is a stand-in the tests create themselves). `.env` files must never be tracked: that is a finding too (only `.env.example`, with empty values, is allowed).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHAPES = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-|live-|ant-)?[A-Za-z0-9_\-]{20,}"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Resend key": re.compile(r"\bre_[A-Za-z0-9]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    "password inside a URL": re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:(?!\$\{|<|\*|password|pass|secret|xxx|changeme|\{)[^/\s:@${<]{4,}@[^/\s]+"),
}
NAMED = re.compile(r"""(?ix)
    \b[\w\-]*(?:api[_\-]?key|secret|passw(?:or)?d|token|credential|private[_\-]?key)[\w\-]*\b\s*[:=]\s*
    (?P<q>["'])(?P<value>[^"'\s]{16,})(?P=q)""")  # no spaces: a sentence is a message, not a key
PLACEHOLDER = re.compile(r"(?i)^(?:\$\{.*|<.*|\{\{.*|your[_\-\s].*|change[_\-]?me.*|example.*|dummy.*|test.*|xxx+.*|\*+|none|null|todo.*|placeholder.*|.*(?:\.\.\.|<[^>]+>).*)$")
SKIP_DIRS = ("tests/", "node_modules/", ".venv/", "data/raw/", "docs/", "uis/website/public/", "services/tests/")
SKIP_NAMES = ("package-lock.json", "test_", "conftest.py", "_test.py", ".test.", ".spec.", "fixtures")
SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".toml", ".env", ".sh", ".ps1", ".cfg", ".ini", ".html", ".md", ".txt", ".example", ""}
SELF = "scripts/security/secret_scan.py"


def _looks_like_secret(value: str) -> bool:
    """Letters AND digits (or symbols), no word separators of a label: `trackflow-access-token` is a name, `k3y9Zx...` is a key."""
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def files(include_untracked: bool) -> list[Path]:
    command = ["git", "ls-files"] + (["--others", "--exclude-standard", "--cached"] if include_untracked else [])
    out = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    return [ROOT / name for name in out if name]


def scan(include_untracked: bool = False) -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for path in files(include_untracked):
        rel = path.relative_to(ROOT).as_posix()
        if rel == SELF or not path.is_file():
            continue
        name = path.name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            findings.append((rel, 0, "env file tracked", "environment files must not be committed (only .env.example)"))
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES or any(rel.startswith(d) or f"/{d}" in rel for d in SKIP_DIRS) or any(part in rel for part in SKIP_NAMES) or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in SHAPES.items():
                if pattern.search(line):
                    findings.append((rel, number, label, line.strip()[:80]))
            if path.suffix.lower() == ".md":
                continue  # documentation shows shapes of examples; the shapes above still apply to it
            match = NAMED.search(line)
            if match and _looks_like_secret(match.group("value")) and not PLACEHOLDER.match(match.group("value").strip()) and "getenv" not in line and "environ" not in line:
                findings.append((rel, number, "secret-looking value assigned to a secret-looking name", line.strip()[:80]))
    return findings


def main() -> int:
    findings = scan("--all" in sys.argv)
    for rel, number, label, excerpt in findings:
        print(f"{rel}:{number}: {label}: {excerpt}")
    print(f"\n{len(findings)} finding(s) in {len(files('--all' in sys.argv))} files scanned." if findings else f"No credentials found in {len(files('--all' in sys.argv))} files.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
