"""Autenticación JWT sin estado: login, `get_current_user` y comprobaciones de acceso.

Las rutas viven bajo `/auth`. Los usuarios se gestionan en `/users` y los perfiles en `/profiles`.
"""

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer

from .account_schemas import LoginRequest, SessionUser, TokenResponse, UserCreate, UserWithProfile
from .accounts import (
    AccountStoreError,
    EmailAlreadyRegisteredError,
    Role,
    User,
    create_user,
    get_profile_by_user_id,
    get_user_by_email,
    get_user_by_id,
    update_user,
)
from .security import (
    InvalidTokenError,
    burn_password_check,
    create_access_token,
    decode_access_token,
    token_lifetime,
    verify_password,
)

logger = logging.getLogger("services.auth")

router = APIRouter(prefix="/auth", tags=["authentication"])
# auto_error=False para devolver nuestros propios mensajes en lugar de "Not authenticated".
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

ACCOUNT_SERVICE_UNAVAILABLE = "The account service is temporarily unavailable. Please try again in a moment."
EMAIL_TAKEN = "An account with this email already exists."


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User:
    """Dependencia reutilizable: decodifica el JWT y devuelve la cuenta autenticada.

    401 si falta el token, es inválido o ha caducado, o la cuenta ya no existe; 403 si está desactivada.
    """
    if token is None:
        raise _unauthorized("Authentication is required.")
    try:
        user_id = decode_access_token(token)
    except InvalidTokenError as error:
        raise _unauthorized("Your session is invalid or has expired. Please log in again.") from error
    try:
        user = get_user_by_id(user_id)
    except AccountStoreError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=ACCOUNT_SERVICE_UNAVAILABLE) from error
    if user is None:
        raise _unauthorized("The account for this session no longer exists.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled.")
    return user


def require_self_or_admin(current_user: User, target_user_id: str) -> None:
    """403 salvo que el usuario actual sea el propio objetivo o un administrador."""
    if current_user.id != target_user_id and current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this account.")


def require_admin(current_user: User) -> None:
    """403 salvo que el usuario actual sea administrador."""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action is restricted to administrators.")


def _session_response(user: User) -> TokenResponse:
    profile = get_profile_by_user_id(user.id)
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=int(token_lifetime().total_seconds()),
        user=SessionUser(id=user.id, email=user.email, name=profile.name if profile else user.email, role=user.role),
    )


def authenticate(email: str, password: str) -> User:
    """Valida credenciales. Mismo error y tiempo de respuesta si el email no existe o la clave falla."""
    user = get_user_by_email(email)
    if user is None:
        burn_password_check(password)
        raise _unauthorized("Email or password is incorrect.")
    matches, needs_rehash = verify_password(password, user.hashed_password)
    if not matches:
        raise _unauthorized("Email or password is incorrect.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled.")
    if needs_rehash:
        update_user(user.id, password=password)  # cuentas anteriores a bcrypt: mejora el hash en silencio
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    """Valida email y contraseña y devuelve un JWT firmado."""
    return _session_response(authenticate(payload.email, payload.password))


@router.post("/token", include_in_schema=False)
async def login_for_swagger(request: Request) -> dict[str, str]:
    """Login con formulario (`username` + `password`) para el botón Authorize de Swagger.

    Se lee el cuerpo `x-www-form-urlencoded` a mano para no añadir python-multipart como dependencia.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    username, password = (form.get("username") or [""])[0], (form.get("password") or [""])[0]
    if not username or not password:
        raise HTTPException(status_code=422, detail="Email and password are required.")
    user = await run_in_threadpool(authenticate, username, password)  # bcrypt y TinyDB son bloqueantes
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@router.post("/register", response_model=UserWithProfile, status_code=status.HTTP_201_CREATED, deprecated=True)
def register_alias(payload: UserCreate) -> dict:
    """Alias antiguo de `POST /users`; se mantiene para que el backoffice actual siga funcionando."""
    try:
        user, profile = create_user(
            payload.email, payload.password, name=payload.name, phone=payload.phone, address=payload.address
        )
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from error
    return {**user.model_dump(), "profile": profile.model_dump()}
