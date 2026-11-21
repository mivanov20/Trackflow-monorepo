"""Hash de contraseñas y firma/validación de JWT. No accede a ningún almacenamiento."""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from jose import JWTError, jwt
from passlib.hash import bcrypt

ALLOWED_ALGORITHMS = ("HS256", "HS384", "HS512")
DEFAULT_ALGORITHM = "HS256"
DEFAULT_EXPIRE_MINUTES = 480
DEFAULT_BCRYPT_ROUNDS = 12
MAX_PASSWORD_BYTES = 72  # límite real de bcrypt
LEGACY_PBKDF2_ITERATIONS = 310_000


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


def validate_security_config() -> None:
    """Falla al arrancar si falta o es inválida la configuración de seguridad."""
    jwt_secret()
    jwt_algorithm()
    token_lifetime()


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


def decode_access_token(token: str) -> str:
    """Valida firma y caducidad y devuelve el id de usuario; lanza InvalidTokenError si falla."""
    try:
        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[jwt_algorithm()],
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as error:
        raise InvalidTokenError("Token is invalid or expired.") from error
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidTokenError("Token subject is missing.")
    return user_id
