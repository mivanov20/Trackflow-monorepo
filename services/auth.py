"""Autenticación JWT sin estado: login, `get_current_user` y comprobaciones de acceso.

Las rutas viven bajo `/auth`. Los usuarios se gestionan en `/users` y los perfiles en `/profiles`.
"""

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer

from .account_schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SessionUser,
    TokenResponse,
    UserCreate,
    UserWithProfile,
)
from .accounts import (
    AccountStoreError,
    EmailAlreadyRegisteredError,
    Role,
    User,
    complete_password_reset,
    create_user,
    get_profile_by_user_id,
    get_user_by_email,
    get_user_by_id,
    register_reset_request,
    update_user,
)
from . import hardening
from .mailer import build_reset_email, reset_link, send_email
from .security import (
    InvalidTokenError,
    burn_password_check,
    create_access_token,
    create_reset_token,
    decode_access_token,
    decode_reset_token,
    reset_cooldown_seconds,
    reset_token_lifetime,
    token_lifetime,
    verify_password,
)

logger = logging.getLogger("services.auth")

router = APIRouter(prefix="/auth", tags=["authentication"])
# auto_error=False para devolver nuestros propios mensajes en lugar de "Not authenticated".
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

ACCOUNT_SERVICE_UNAVAILABLE = "The account service is temporarily unavailable. Please try again in a moment."
EMAIL_TAKEN = "An account with this email already exists."
# Misma respuesta exista o no la cuenta: no se puede averiguar qué correos están registrados.
FORGOT_PASSWORD_MESSAGE = "If that address is registered, you will receive a reset link shortly."
INVALID_RESET_LINK = "This reset link is invalid or has expired. Request a new one."


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User:
    """Dependencia reutilizable: decodifica el JWT y devuelve la cuenta autenticada.

    401 si falta el token, es inválido o ha caducado, o la cuenta ya no existe; 403 si está desactivada.
    """
    if token is None:
        raise _unauthorized("Authentication is required.")
    try:
        claims = decode_access_token(token)
    except InvalidTokenError as error:
        raise _unauthorized("Your session is invalid or has expired. Please log in again.") from error
    try:
        user = get_user_by_id(claims.user_id)
    except AccountStoreError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=ACCOUNT_SERVICE_UNAVAILABLE) from error
    if user is None:
        raise _unauthorized("The account for this session no longer exists.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled.")
    if user.password_changed_at is not None and (
        claims.issued_at is None or claims.issued_at < int(user.password_changed_at.timestamp())
    ):
        # La contraseña cambió después de emitir este token: se cierra esa sesión.
        raise _unauthorized("Your password was changed. Please sign in again.")
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


def throttled_authenticate(request: Request, email: str, password: str) -> User:
    """`authenticate` behind the sign-in throttle: a client that keeps failing is stopped before the password is even checked."""
    hardening.check_login_allowed(request, email)
    try:
        user = authenticate(email, password)
    except HTTPException as error:
        if error.status_code == status.HTTP_401_UNAUTHORIZED:
            hardening.record_login_failure(request, email)
        raise
    hardening.record_login_success(request, email)
    return user


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
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    """Valida email y contraseña y devuelve un JWT firmado. Tras demasiados fallos seguidos responde 429 (services/hardening.py)."""
    return _session_response(throttled_authenticate(request, payload.email, payload.password))


@router.get("/me", response_model=UserWithProfile)
def read_current_account(current_user: User = Depends(get_current_user)) -> dict:
    """Devuelve el email y rol del usuario autenticado junto con los datos de su perfil.

    El frontend lo usa para validar el token al entrar a una vista protegida y para la página de cuenta.
    """
    profile = get_profile_by_user_id(current_user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    return {**current_user.model_dump(), "profile": profile.model_dump()}


@router.post("/token", include_in_schema=False)
async def login_for_swagger(request: Request) -> dict[str, str]:
    """Login con formulario (`username` + `password`) para el botón Authorize de Swagger.

    Se lee el cuerpo `x-www-form-urlencoded` a mano para no añadir python-multipart como dependencia.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    username, password = (form.get("username") or [""])[0], (form.get("password") or [""])[0]
    if not username or not password:
        raise HTTPException(status_code=422, detail="Email and password are required.")
    user = await run_in_threadpool(throttled_authenticate, request, username, password)  # bcrypt y TinyDB son bloqueantes
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


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Pide un enlace de recuperación. Siempre responde 200 con el mismo mensaje, exista o no el email.

    El correo se envía en segundo plano para que la respuesta no tarde distinto si la cuenta existe.
    """
    user = get_user_by_email(payload.email)
    if user is not None and user.is_active:
        reset = create_reset_token(user.id)
        if register_reset_request(user.id, reset.jti, reset.expires_at, reset_cooldown_seconds()):
            profile = get_profile_by_user_id(user.id)
            minutes = round(reset_token_lifetime().total_seconds() / 60)
            message = build_reset_email(user.email, profile.name if profile else "", reset_link(reset.token), minutes)
            background_tasks.add_task(send_email, message)
    return {"message": FORGOT_PASSWORD_MESSAGE}


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> dict[str, str]:
    """Cambia la contraseña con el token del correo. 400 si es inválido, caducó o ya se usó."""
    try:
        user_id, jti = decode_reset_token(payload.token)
    except InvalidTokenError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_RESET_LINK) from error
    if complete_password_reset(user_id, jti, payload.new_password) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_RESET_LINK)
    return {"message": "Your password was updated. You can now sign in with it."}


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(payload: ChangePasswordRequest, current_user: User = Depends(get_current_user)) -> dict[str, object]:
    """Cambia la contraseña de la sesión actual, tras comprobar la actual. Devuelve un token nuevo."""
    matches, _ = verify_password(payload.current_password, current_user.hashed_password)
    if not matches:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Your current password is incorrect.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a new password that is different from your current one.")
    update_user(current_user.id, password=payload.new_password, revoke_sessions=True)
    return {
        "message": "Your password was changed.",
        "access_token": create_access_token(current_user.id),
        "token_type": "bearer",
        "expires_in": int(token_lifetime().total_seconds()),
    }
