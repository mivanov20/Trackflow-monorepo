"""Hash de contraseñas y firma/validación de JWT. No accede a ningún almacenamiento."""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from jwt import PyJWTError as JWTError  # PyJWT (python-jose has an unfixed advisory, PYSEC-2026-4298)
from passlib.hash import bcrypt

ALLOWED_ALGORITHMS = ("HS256", "HS384", "HS512")
DEFAULT_ALGORITHM = "HS256"
DEFAULT_EXPIRE_MINUTES = 480
DEFAULT_BCRYPT_ROUNDS = 12
MAX_PASSWORD_BYTES = 72  # límite real de bcrypt
LEGACY_PBKDF2_ITERATIONS = 310_000
RESET_PURPOSE = "password_reset"
DEFAULT_RESET_EXPIRE_MINUTES = 30
DEFAULT_RESET_COOLDOWN_SECONDS = 60


class InvalidTokenError(Exception):
    """El token no es válido: firma incorrecta, caducado o con claims incompletos."""


def jwt_secret() -> str:
    """Lee la clave de firma de JWT_SECRET (mínimo 32 caracteres); nunca está en el código."""
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be configured with at least 32 characters before starting the API.")
    return secret


def jwt_algorithm() -> str:
    """Algoritmo de firma de JWT_ALGORITHM; solo se aceptan variantes HMAC."""
    algorithm = os.getenv("JWT_ALGORITHM", DEFAULT_ALGORITHM)
    if algorithm not in ALLOWED_ALGORITHMS:
        raise RuntimeError(f"JWT_ALGORITHM must be one of: {', '.join(ALLOWED_ALGORITHMS)}.")
    return algorithm


def token_lifetime() -> timedelta:
    """Ventana de validez del token según ACCESS_TOKEN_EXPIRE_MINUTES."""
    raw = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(DEFAULT_EXPIRE_MINUTES))
    try:
        minutes = int(raw)
    except ValueError as error:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be a whole number of minutes.") from error
    if minutes < 1:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be at least 1.")
    return timedelta(minutes=minutes)


def reset_token_lifetime() -> timedelta:
    """Validez del enlace de recuperación según PASSWORD_RESET_EXPIRE_MINUTES (recomendado: 15 a 60)."""
    raw = os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", str(DEFAULT_RESET_EXPIRE_MINUTES))
    try:
        minutes = int(raw)
    except ValueError as error:
        raise RuntimeError("PASSWORD_RESET_EXPIRE_MINUTES must be a whole number of minutes.") from error
    if not 1 <= minutes <= 1440:
        raise RuntimeError("PASSWORD_RESET_EXPIRE_MINUTES must be between 1 and 1440.")
    return timedelta(minutes=minutes)


def reset_cooldown_seconds() -> int:
    """Segundos mínimos entre dos correos de recuperación para la misma cuenta (0 desactiva el límite)."""
    raw = os.getenv("PASSWORD_RESET_COOLDOWN_SECONDS", str(DEFAULT_RESET_COOLDOWN_SECONDS))
    try:
        seconds = int(raw)
    except ValueError as error:
        raise RuntimeError("PASSWORD_RESET_COOLDOWN_SECONDS must be a whole number of seconds.") from error
    if seconds < 0:
        raise RuntimeError("PASSWORD_RESET_COOLDOWN_SECONDS cannot be negative.")
    return seconds


def validate_security_config() -> None:
    """Falla al arrancar si falta o es inválida la configuración de seguridad."""
    jwt_secret()
    jwt_algorithm()
    token_lifetime()
    reset_token_lifetime()
    reset_cooldown_seconds()


def _bcrypt_rounds() -> int:
    """Coste de bcrypt; configurable para que los tests no tarden (mínimo 4, el de bcrypt)."""
    try:
        return max(4, int(os.getenv("BCRYPT_ROUNDS", str(DEFAULT_BCRYPT_ROUNDS))))
    except ValueError:
        return DEFAULT_BCRYPT_ROUNDS


def password_fits_bcrypt(password: str) -> bool:
    """True si la contraseña cabe en los 72 bytes que bcrypt procesa."""
    return len(password.encode("utf-8")) <= MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    """Devuelve el hash bcrypt (con sal propia) de la contraseña."""
    return bcrypt.using(rounds=_bcrypt_rounds()).hash(password)


def _legacy_pbkdf2_matches(password: str, stored_hash: str) -> bool:
    """Compara contra el formato antiguo `sal$digest` (PBKDF2) de cuentas creadas antes de bcrypt."""
    try:
        salt, expected = stored_hash.split("$", maxsplit=1)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), LEGACY_PBKDF2_ITERATIONS
        ).hex()
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Comprueba la contraseña; devuelve `(coincide, conviene_rehashear)`.

    Los hashes bcrypt empiezan por `$2`. Los de formato antiguo siguen siendo válidos y se
    marcan para re-hashear con bcrypt tras un login correcto.
    """
    if stored_hash.startswith("$2"):
        try:
            return bcrypt.verify(password, stored_hash), False
        except ValueError:  # hash corrupto o contraseña de más de 72 bytes
            return False, False
    matches = _legacy_pbkdf2_matches(password, stored_hash)
    return matches, matches


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("timing-equalizer-not-a-real-password")


def burn_password_check(password: str) -> None:
    """Gasta el mismo tiempo que una verificación real para no revelar si un email existe."""
    verify_password(password, _dummy_hash())


def create_access_token(user_id: str) -> str:
    """Firma un JWT con el id del usuario en `sub` y caducidad configurable."""
    now = datetime.now(timezone.utc)
    claims = {"sub": user_id, "iat": now, "exp": now + token_lifetime()}
    return jwt.encode(claims, jwt_secret(), algorithm=jwt_algorithm())


@dataclass(frozen=True)
class TokenClaims:
    """Lo que el servidor necesita de un token de sesión válido."""

    user_id: str
    issued_at: int | None


def decode_access_token(token: str) -> TokenClaims:
    """Valida firma y caducidad de un token de SESIÓN; lanza InvalidTokenError si falla.

    Un token con claim `purpose` (por ejemplo el de recuperación de contraseña) nunca vale como sesión.
    """
    try:
        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[jwt_algorithm()],
            options={"require": ["exp", "sub"]},
        )
    except JWTError as error:
        raise InvalidTokenError("Token is invalid or expired.") from error
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id or "purpose" in claims:
        raise InvalidTokenError("Token subject is missing.")
    issued_at = claims.get("iat")
    return TokenClaims(user_id=user_id, issued_at=issued_at if isinstance(issued_at, int) else None)


@dataclass(frozen=True)
class ResetToken:
    """Token de recuperación recién emitido y los datos que se guardan en el servidor."""

    token: str
    jti: str
    expires_at: datetime


def hash_jti(jti: str) -> str:
    """El servidor guarda solo el hash del identificador del token, no el identificador."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def create_reset_token(user_id: str) -> ResetToken:
    """Firma un JWT de un solo propósito (`password_reset`) con caducidad corta e identificador único."""
    now = datetime.now(timezone.utc)
    expires_at = now + reset_token_lifetime()
    jti = secrets.token_urlsafe(16)
    claims = {"sub": user_id, "purpose": RESET_PURPOSE, "jti": jti, "iat": now, "exp": expires_at}
    return ResetToken(jwt.encode(claims, jwt_secret(), algorithm=jwt_algorithm()), jti, expires_at)


def decode_reset_token(token: str) -> tuple[str, str]:
    """Valida firma, caducidad y propósito; devuelve `(user_id, jti)` o lanza InvalidTokenError.

    Que no se haya usado antes se comprueba aparte, contra el registro del servidor.
    """
    try:
        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[jwt_algorithm()],
            options={"require": ["exp", "sub"]},
        )
    except JWTError as error:
        raise InvalidTokenError("Reset token is invalid or expired.") from error
    user_id, jti = claims.get("sub"), claims.get("jti")
    if claims.get("purpose") != RESET_PURPOSE or not isinstance(user_id, str) or not isinstance(jti, str):
        raise InvalidTokenError("Not a password reset token.")
    return user_id, jti
