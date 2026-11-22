"""Modelos User/Profile y capa de servicios sobre TinyDB.

Usuarios y perfiles viven únicamente en TinyDB (nunca en PostgreSQL). `User` guarda solo
credenciales; nombre, teléfono y dirección pertenecen a `Profile`, ligado 1:1 por `user_id`.
"""

import logging
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel
from tinydb import Query, TinyDB

from .security import hash_jti, hash_password

logger = logging.getLogger("services.accounts")

USERS_TABLE = "users"
PROFILES_TABLE = "profiles"
RESETS_TABLE = "password_resets"  # un registro por enlace de recuperación emitido
LEGACY_TABLE = "_default"  # donde guardaba las cuentas la versión anterior

# TinyDB no es seguro ante escrituras concurrentes y FastAPI ejecuta las rutas síncronas en hilos.
_store_lock = threading.RLock()


class Role(str, Enum):
    """Roles permitidos; cualquier otro valor se rechaza al validar."""

    admin = "admin"
    manager = "manager"
    user = "user"


class User(BaseModel):
    """Credenciales de una cuenta. No incluye nombre ni datos de contacto."""

    id: str
    email: str
    hashed_password: str
    is_active: bool = True
    role: Role = Role.user
    created_at: datetime
    # Se fija al restablecer o cambiar la contraseña: las sesiones emitidas antes dejan de valer.
    password_changed_at: datetime | None = None


class Profile(BaseModel):
    """Datos de la persona; exactamente uno por usuario."""

    id: str
    user_id: str
    name: str
    phone: str = ""
    address: str = ""


class EmailAlreadyRegisteredError(Exception):
    """Ya existe otra cuenta con ese email."""


class LastAdminError(Exception):
    """La operación dejaría el sistema sin ningún administrador activo."""


class AccountStoreError(Exception):
    """El almacén de cuentas no se puede leer o escribir; el detalle solo va al log."""


def normalize_email(email: str) -> str:
    """Los emails se comparan sin espacios ni mayúsculas."""
    return str(email).strip().lower()


def _users_path() -> Path:
    """Prepara la ruta privada del archivo TinyDB de cuentas."""
    default_path = Path.home() / ".local" / "share" / "trackflow" / "users.json"
    path = Path(os.getenv("AUTH_DB_PATH", str(default_path))).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path.chmod(0o600)
    return path


def _migrate_legacy_accounts(database: TinyDB) -> None:
    """Convierte las cuentas del formato anterior (tabla por defecto) a users + profiles.

    El `user_uuid` antiguo pasa a ser `id`, así los registros de inventario ya creados siguen
    apuntando al mismo usuario. El hash PBKDF2 se conserva y se mejora a bcrypt en el primer login.
    """
    legacy = database.table(LEGACY_TABLE)
    documents = legacy.all()
    if not documents:
        return
    users, profiles = database.table(USERS_TABLE), database.table(PROFILES_TABLE)
    for document in documents:
        legacy_id, email, hashed = document.get("user_uuid"), document.get("email"), document.get("password_hash")
        if not (legacy_id and email and hashed):
            continue  # documento ajeno al formato conocido: no se toca
        if not users.contains(Query().id == legacy_id):
            users.insert(
                User(
                    id=legacy_id,
                    email=normalize_email(email),
                    hashed_password=hashed,
                    created_at=datetime.now(timezone.utc),
                ).model_dump(mode="json")
            )
            profiles.insert(
                Profile(id=str(uuid.uuid4()), user_id=legacy_id, name=document.get("name") or email.split("@")[0]).model_dump()
            )
        legacy.remove(doc_ids=[document.doc_id])


@contextmanager
def _open_store() -> Iterator[TinyDB]:
    """Abre el archivo de cuentas en exclusiva; los fallos de I/O o JSON corrupto pasan a AccountStoreError."""
    with _store_lock:
        try:
            with TinyDB(_users_path()) as database:
                _migrate_legacy_accounts(database)
                yield database
            path = _users_path()
            if path.exists():
                path.chmod(0o600)
        except (OSError, ValueError) as error:  # JSONDecodeError y ValidationError son ValueError
            logger.exception("Account store unavailable")
            raise AccountStoreError("The account store could not be read or written.") from error


def _other_active_admin_exists(database: TinyDB, excluded_user_id: str) -> bool:
    return any(
        document["id"] != excluded_user_id and document.get("role") == Role.admin.value and document.get("is_active", True)
        for document in database.table(USERS_TABLE).all()
    )


# --- servicios de usuario -------------------------------------------------------


def create_user(
    email: str,
    password: str,
    *,
    role: Role = Role.user,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> tuple[User, Profile]:
    """Crea la cuenta (con la contraseña hasheada) y su perfil en la misma operación."""
    role = Role(role)
    email = normalize_email(email)
    hashed = hash_password(password)  # fuera del bloqueo: bcrypt es lento a propósito
    user = User(id=str(uuid.uuid4()), email=email, hashed_password=hashed, role=role, created_at=datetime.now(timezone.utc))
    profile = Profile(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name=(name or "").strip() or email.split("@")[0],
        phone=(phone or "").strip(),
        address=(address or "").strip(),
    )
    with _open_store() as database:
        users = database.table(USERS_TABLE)
        if users.contains(Query().email == email):
            raise EmailAlreadyRegisteredError(email)
        users.insert(user.model_dump(mode="json"))
        try:
            database.table(PROFILES_TABLE).insert(profile.model_dump())
        except Exception:
            users.remove(Query().id == user.id)  # no dejar un usuario sin perfil
            raise
    return user, profile


def get_user_by_id(user_id: str) -> User | None:
    """Devuelve el usuario con ese id o None."""
    with _open_store() as database:
        document = database.table(USERS_TABLE).get(Query().id == user_id)
    return User.model_validate(document) if document else None


def get_user_by_email(email: str) -> User | None:
    """Devuelve el usuario con ese email (sin distinguir mayúsculas) o None."""
    with _open_store() as database:
        document = database.table(USERS_TABLE).get(Query().email == normalize_email(email))
    return User.model_validate(document) if document else None


def list_users() -> list[User]:
    """Lista todas las cuentas, las más antiguas primero."""
    with _open_store() as database:
        documents = database.table(USERS_TABLE).all()
    return sorted((User.model_validate(document) for document in documents), key=lambda user: user.created_at)


def update_user(
    user_id: str,
    *,
    email: str | None = None,
    password: str | None = None,
    role: Role | None = None,
    is_active: bool | None = None,
    revoke_sessions: bool = False,
) -> User | None:
    """Actualiza credenciales; devuelve None si el usuario no existe.

    Con `revoke_sessions=True` y una contraseña nueva, las sesiones anteriores quedan invalidadas.
    """
    role = Role(role) if role is not None else None
    new_hash = hash_password(password) if password is not None else None
    new_email = normalize_email(email) if email is not None else None
    with _open_store() as database:
        users = database.table(USERS_TABLE)
        document = users.get(Query().id == user_id)
        if document is None:
            return None
        changes: dict[str, object] = {}
        if new_email is not None:
            if users.get((Query().email == new_email) & (Query().id != user_id)):
                raise EmailAlreadyRegisteredError(new_email)
            changes["email"] = new_email
        if new_hash is not None:
            changes["hashed_password"] = new_hash
            if revoke_sessions:
                changes["password_changed_at"] = datetime.now(timezone.utc).isoformat()
        if role is not None:
            changes["role"] = role.value
        if is_active is not None:
            changes["is_active"] = is_active
        stops_being_active_admin = document.get("role") == Role.admin.value and document.get("is_active", True) and (
            changes.get("role", document.get("role")) != Role.admin.value or not changes.get("is_active", True)
        )
        if stops_being_active_admin and not _other_active_admin_exists(database, user_id):
            raise LastAdminError(user_id)
        if changes:
            users.update(changes, Query().id == user_id)
        return User.model_validate({**document, **changes})


def delete_user(user_id: str) -> bool:
    """Borra la cuenta y su perfil; devuelve False si no existía."""
    with _open_store() as database:
        users = database.table(USERS_TABLE)
        document = users.get(Query().id == user_id)
        if document is None:
            return False
        if (
            document.get("role") == Role.admin.value
            and document.get("is_active", True)
            and not _other_active_admin_exists(database, user_id)
        ):
            raise LastAdminError(user_id)
        users.remove(Query().id == user_id)
        database.table(PROFILES_TABLE).remove(Query().user_id == user_id)
    return True


# --- servicios de perfil --------------------------------------------------------


def get_profile_by_user_id(user_id: str) -> Profile | None:
    """Devuelve el perfil ligado a ese usuario o None."""
    with _open_store() as database:
        document = database.table(PROFILES_TABLE).get(Query().user_id == user_id)
    return Profile.model_validate(document) if document else None


def update_profile(
    user_id: str, *, name: str | None = None, phone: str | None = None, address: str | None = None
) -> Profile | None:
    """Actualiza nombre, teléfono y dirección; devuelve None si el perfil no existe."""
    changes = {
        key: value.strip()
        for key, value in (("name", name), ("phone", phone), ("address", address))
        if value is not None
    }
    with _open_store() as database:
        profiles = database.table(PROFILES_TABLE)
        document = profiles.get(Query().user_id == user_id)
        if document is None:
            return None
        if changes:
            profiles.update(changes, Query().user_id == user_id)
        return Profile.model_validate({**document, **changes})


# --- recuperación de contraseña -------------------------------------------------


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def register_reset_request(user_id: str, jti: str, expires_at: datetime, cooldown_seconds: int) -> bool:
    """Guarda el enlace emitido (solo el hash de su id) y anula los anteriores de la cuenta.

    Devuelve False, sin guardar nada, si ya se emitió uno hace menos de `cooldown_seconds`
    (limita el envío masivo de correos a una misma cuenta).
    """
    now = datetime.now(timezone.utc)
    with _open_store() as database:
        resets = database.table(RESETS_TABLE)
        for stale in [doc for doc in resets.all() if _parse_time(doc["expires_at"]) < now - timedelta(days=1)]:
            resets.remove(doc_ids=[stale.doc_id])  # limpieza: los registros caducados hace más de un día sobran
        previous = resets.search(Query().user_id == user_id)
        if cooldown_seconds > 0 and any(_parse_time(doc["created_at"]) > now - timedelta(seconds=cooldown_seconds) for doc in previous):
            return False
        for doc in previous:
            if doc["used_at"] is None:
                resets.update({"used_at": now.isoformat()}, doc_ids=[doc.doc_id])
        resets.insert(
            {
                "user_id": user_id,
                "jti_hash": hash_jti(jti),
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "used_at": None,
            }
        )
    return True


def complete_password_reset(user_id: str, jti: str, new_password: str) -> User | None:
    """Gasta el enlace y cambia la contraseña en una sola operación; None si el enlace no es utilizable.

    No es utilizable si no existe, ya se usó, caducó, la cuenta no existe o está desactivada.
    Al terminar, cualquier otro enlace pendiente de la cuenta también queda anulado y las sesiones
    anteriores dejan de valer (`password_changed_at`).
    """
    new_hash = hash_password(new_password)  # fuera del bloqueo: bcrypt es lento a propósito
    now = datetime.now(timezone.utc)
    with _open_store() as database:
        resets, users = database.table(RESETS_TABLE), database.table(USERS_TABLE)
        record = resets.get((Query().user_id == user_id) & (Query().jti_hash == hash_jti(jti)))
        if record is None or record["used_at"] is not None or _parse_time(record["expires_at"]) <= now:
            return None
        document = users.get(Query().id == user_id)
        if document is None or not document.get("is_active", True):
            return None
        changes = {"hashed_password": new_hash, "password_changed_at": now.isoformat()}
        users.update(changes, Query().id == user_id)
        for pending in resets.search((Query().user_id == user_id) & (Query().used_at == None)):  # noqa: E711 (TinyDB)
            resets.update({"used_at": now.isoformat()}, doc_ids=[pending.doc_id])
        return User.model_validate({**document, **changes})
