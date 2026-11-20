import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from tinydb import Query, TinyDB

router = APIRouter(prefix="/auth", tags=["authentication"])
bearer_scheme = HTTPBearer(auto_error=False)
JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME_HOURS = 8
PBKDF2_ITERATIONS = 310_000


def _jwt_secret() -> str:
    """Obtiene una clave JWT configurada con longitud mínima segura."""
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be configured with at least 32 characters before starting the API.")
    return secret


def _users_path() -> Path:
    """Prepara la ruta privada de TinyDB para las cuentas operativas."""
    default_path = Path.home() / ".local" / "share" / "trackflow" / "users.json"
    path = Path(os.getenv("AUTH_DB_PATH", str(default_path))).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path.chmod(0o600)
    return path


def _hash_password(password: str, salt: str | None = None) -> str:
    """Genera un hash PBKDF2 con sal aleatoria para una contraseña."""
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_value), PBKDF2_ITERATIONS
    ).hex()
    return f"{salt_value}${digest}"


def _password_matches(password: str, stored_hash: str) -> bool:
    """Compara de forma constante una contraseña con su hash almacenado."""
    try:
        salt, expected = stored_hash.split("$", maxsplit=1)
    except ValueError:
        return False
    actual = _hash_password(password, salt).split("$", maxsplit=1)[1]
    return hmac.compare_digest(actual, expected)


def _public_user(user: dict[str, Any]) -> dict[str, str]:
    """Expone únicamente los campos públicos de una cuenta operativa."""
    return {"user_uuid": user["user_uuid"], "name": user["name"], "email": user["email"]}


def _create_access_token(user_uuid: str) -> str:
    """Emite un bearer JWT con expiración limitada para el usuario."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_uuid,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_LIFETIME_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    user_uuid: str
    name: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterRequest) -> dict[str, str]:
    """Registra una cuenta operativa en TinyDB sin crear un usuario SQL."""
    email = str(payload.email).strip().lower()
    with TinyDB(_users_path()) as database:
        users = Query()
        if database.contains(users.email == email):
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user = {
            "user_uuid": str(uuid.uuid4()),
            "name": payload.name.strip(),
            "email": email,
            "password_hash": _hash_password(payload.password),
        }
        database.insert(user)
        _users_path().chmod(0o600)
    return _public_user(user)


@router.post("/login", response_model=TokenResponse)
def login_user(payload: LoginRequest) -> dict[str, Any]:
    """Valida credenciales y devuelve un JWT de acceso."""
    email = str(payload.email).strip().lower()
    with TinyDB(_users_path()) as database:
        user = database.get(Query().email == email)
    if not user or not _password_matches(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": _create_access_token(user["user_uuid"]),
        "token_type": "bearer",
        "user": _public_user(user),
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str]:
    """Valida el bearer JWT y devuelve la cuenta operativa autenticada."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(credentials.credentials, _jwt_secret(), algorithms=[JWT_ALGORITHM])
        user_uuid = payload.get("sub")
        if not isinstance(user_uuid, str):
            raise jwt.InvalidTokenError("Invalid token subject")
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    with TinyDB(_users_path()) as database:
        user = database.get(Query().user_uuid == user_uuid)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The account for this session no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _public_user(user)