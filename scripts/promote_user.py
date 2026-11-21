"""Give an existing account a role (admin, manager or user) from the command line.

Usage (repository root, with the same AUTH_DB_PATH as the API, if you set one):

    services/.venv/Scripts/python scripts/promote_user.py someone@example.com admin

The API cannot create the first administrator on purpose: registration always yields `user`
and only an admin can change roles, so the first admin is promoted here.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.accounts import AccountStoreError, LastAdminError, Role, get_user_by_email, update_user  # noqa: E402


def main(argv: list[str]) -> int:
    """Promote the account in `argv[1]` to the role in `argv[2]`; returns the exit code."""
    if len(argv) != 3:
        print("Usage: promote_user.py <email> <admin|manager|user>", file=sys.stderr)
        return 1
    email, wanted = argv[1], argv[2]
    try:
        role = Role(wanted)
    except ValueError:
        print(f"Unknown role '{wanted}'. Use one of: {', '.join(r.value for r in Role)}.", file=sys.stderr)
        return 1
    try:
        user = get_user_by_email(email)
        if user is None:
            print(f"No account with email {email}.", file=sys.stderr)
            return 1
        update_user(user.id, role=role)
    except LastAdminError:
        print("That would leave the system without an active administrator.", file=sys.stderr)
        return 1
    except AccountStoreError:
        print("The account store could not be read or written. Check AUTH_DB_PATH and file permissions.", file=sys.stderr)
        return 1
    print(f"{email} is now {role.value}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
