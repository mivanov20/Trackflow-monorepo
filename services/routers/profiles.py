"""Perfiles (`/profiles`): nombre y datos de contacto del usuario autenticado."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..account_schemas import ProfileRead, ProfileUpdate
from ..accounts import Profile, User, get_profile_by_user_id, update_profile
from ..auth import get_current_user

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _missing_profile() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")


@router.get("/me", response_model=ProfileRead)
def read_my_profile(current_user: User = Depends(get_current_user)) -> Profile:
    """Devuelve el perfil del usuario autenticado."""
    profile = get_profile_by_user_id(current_user.id)
    if profile is None:
        raise _missing_profile()
    return profile


@router.put("/me", response_model=ProfileRead)
def update_my_profile(payload: ProfileUpdate, current_user: User = Depends(get_current_user)) -> Profile:
    """Actualiza nombre, teléfono y dirección. Solo existe la ruta propia: nadie edita perfiles ajenos."""
    profile = update_profile(current_user.id, name=payload.name, phone=payload.phone, address=payload.address)
    if profile is None:
        raise _missing_profile()
    return profile
