"""The first staff account.

Anyone can self-register, and that account has role `user`, which the operational APIs refuse (`auth.require_staff`, audit finding H-03). Without a way to create a first
admin nobody could use the backoffice, so at startup, IF there is no active admin and both variables are set, one is created:

    BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD     (the password is read from the environment, never logged, never printed)

The admin then promotes colleagues with `PUT /users/{id}` (role `manager` or `admin`). The MCP server's service account (`API_SERVICE_EMAIL`) must also be staff
(`manager`), because it calls the incident and inventory APIs. The same thing can be done by hand: `python scripts/create_staff_user.py`.
"""

from __future__ import annotations

import logging
import os

from . import accounts
from .accounts import Role

logger = logging.getLogger("trackflow.security")


def ensure_bootstrap_admin() -> bool:
    """Creates the first admin from the environment when none exists. Returns True when it created one."""
    email, password = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip(), os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not email or not password:
        return False
    if any(user.role == Role.admin and user.is_active for user in accounts.list_users()):
        return False
    try:
        accounts.create_user(email, password, role=Role.admin, name="Administrator")
    except accounts.EmailAlreadyRegisteredError:
        accounts.update_user(accounts.get_user_by_email(email).id, role=Role.admin, is_active=True)
    logger.warning("bootstrap: the first admin account was created for the address in BOOTSTRAP_ADMIN_EMAIL; remove BOOTSTRAP_ADMIN_PASSWORD from the environment now")
    return True
