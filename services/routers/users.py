"""Gestión de usuarios (`/users`): solo credenciales; los datos personales viven en `/profiles`."""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..account_schemas import UserCreate, UserRead, UserUpdate, UserWithProfile
from ..accounts import (
    EmailAlreadyRegisteredError,
    LastAdminError,
    Role,
    User,
    create_user,
    delete_user,
    get_user_by_id,
    list_users,
    update_user,
)
from ..auth import EMAIL_TAKEN, get_current_user, require_admin, require_self_or_admin

router = APIRouter(prefix="/users", tags=["users"])

LAST_ADMIN = "At least one active administrator must remain. Promote another account first."


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")


@router.post("", response_model=UserWithProfile, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate) -> dict:
    """Registro público. Hashea la contraseña, asigna el rol `user` y crea el perfil enlazado."""
    try:
        user, profile = create_user(
            payload.email, payload.password, name=payload.name, phone=payload.phone, address=payload.address
        )
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from error
    return {**user.model_dump(), "profile": profile.model_dump()}


@router.get("", response_model=list[UserRead])
def list_all_users(current_user: User = Depends(get_current_user)) -> list[User]:
    """Lista todas las cuentas; expone emails de terceros, así que solo la ve un administrador."""
    require_admin(current_user)
    return list_users()


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: str, current_user: User = Depends(get_current_user)) -> User:
    """Devuelve una cuenta: la propia o, si eres administrador, cualquiera."""
    require_self_or_admin(current_user, user_id)  # antes de buscar, para no revelar qué ids existen
    user = get_user_by_id(user_id)
    if user is None:
        raise _not_found()
    return user


@router.put("/{user_id}", response_model=UserRead)
def update_user_credentials(user_id: str, payload: UserUpdate, current_user: User = Depends(get_current_user)) -> User:
    """Actualiza email o contraseña (propietario o admin); `role` e `is_active` solo los cambia un admin."""
    require_self_or_admin(current_user, user_id)
    if (payload.role is not None or payload.is_active is not None) and current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can change roles or account status.")
    try:
        user = update_user(
            user_id, email=payload.email, password=payload.password, role=payload.role, is_active=payload.is_active
        )
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from error
    except LastAdminError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN) from error
    if user is None:
        raise _not_found()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user(user_id: str, current_user: User = Depends(get_current_user)) -> Response:
    """Elimina la cuenta y su perfil (propietario o admin)."""
    require_self_or_admin(current_user, user_id)
    try:
        deleted = delete_user(user_id)
    except LastAdminError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN) from error
    if not deleted:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
