"""Reproduces the before/after evidence of the audit: runs the exploit tests against a clean checkout of the BASE ref and against this working tree.

    python scripts/security/before_after.py [--base origin/main] [--out docs/security/evidence/before-after.txt]

The proof tests (services/tests/test_security_audit.py, tests/infra/test_hardening.py) are copied into a throwaway `git worktree` of the base ref, so they run
against the code as it was before the fixes. On the base ref the critical exploits must FAIL; on this tree they must PASS. Exit code 0 only when both hold.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROOFS = ["services/tests/test_security_audit.py", "tests/infra/test_hardening.py"]
NODES = PROOFS  # run the whole proof files


def pytest(cwd: Path) -> tuple[int, str]:
    """Each proof file in its own run: on the base ref a file may not even import (the hardening scripts do not exist there), which must not hide the others."""
    code, output = 0, ""
    for node in NODES:
        result = subprocess.run([sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider", "--no-header", "-rfEp"], cwd=cwd, capture_output=True, text=True)
        code, output = code or result.returncode, output + result.stdout + result.stderr
    return code, output


def summary(output: str) -> tuple[list[str], list[str]]:
    failed = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M)))
    passed = sorted(set(re.findall(r"^PASSED (\S+)", output, re.M)))
    return failed, passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--out", default="docs/security/evidence/before-after.txt")
    args = parser.parse_args()
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    base_sha = subprocess.run(["git", "rev-parse", "--short", args.base], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="audit-base-")) / "tree"
    subprocess.run(["git", "worktree", "add", "--detach", str(work), args.base], cwd=ROOT, check=True, capture_output=True)
    try:
        for proof in PROOFS:
            target = work / proof
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / proof, target)
        before_code, before = pytest(work)
        after_code, after = pytest(ROOT)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(work)], cwd=ROOT, capture_output=True)
        shutil.rmtree(work.parent, ignore_errors=True)

    before_failed, before_passed = summary(before)
    after_failed, after_passed = summary(after)
    say(f"BEFORE  base {args.base} ({base_sha}): exploit tests that FAIL = the vulnerability exists")
    for name in before_failed:
        say(f"  FAIL  {name}")
    say(f"  -> {len(before_failed)} failed, {len(before_passed)} passed")
    say()
    say("AFTER   this working tree (the fixes applied)")
    for name in after_failed:
        say(f"  FAIL  {name}")
    say(f"  -> {len(after_failed)} failed, {len(after_passed)} passed")
    ok = bool(before_failed) and not after_failed and after_code == 0
    say()
    say("RESULT  exploits reproduce on base and are closed on this tree" if ok else "RESULT  UNEXPECTED: see the pytest output above")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
