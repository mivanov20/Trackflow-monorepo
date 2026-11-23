"""Creates (or promotes) a staff account: the way to get the first admin or manager on a server where self-registration only gives role `user`.

    python scripts/create_staff_user.py --email ops@example.com --role admin        # asks for the password without echoing it

The password is typed at the prompt (or read from TRACKFLOW_STAFF_PASSWORD), never passed on the command line, so it does not end up in shell history.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite://")  # the accounts live in the account store, not in the SQL database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["admin", "manager"], default="manager")
    args = parser.parse_args()
    from services import accounts

    existing = accounts.get_user_by_email(args.email)
    if existing:
        accounts.update_user(existing.id, role=accounts.Role(args.role), is_active=True)
        print(f"{args.email} is now {args.role}.")
        return 0
    password = os.getenv("TRACKFLOW_STAFF_PASSWORD") or getpass.getpass("Password (not shown): ")
    accounts.create_user(args.email, password, role=accounts.Role(args.role), name=args.email.split("@")[0])
    print(f"Created {args.role} {args.email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
