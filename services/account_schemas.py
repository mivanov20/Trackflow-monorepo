"""Esquemas de entrada y salida de /auth, /users y /profiles."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .accounts import Role
from .security import password_fits_bcrypt


def _check_password(value: str) -> str:
    if not password_fits_bcrypt(value):
        raise ValueError("Password is too long: use at most 72 bytes (about 72 ASCII characters).")
    return value


def _check_optional_password(value: str | None) -> str | None:
    return value if value is None else _check_password(value)


class _Strict(BaseModel):
    """Rechaza campos desconocidos: así un cliente no puede colar `role` o `is_active` donde no toca."""

    model_config = ConfigDict(extra="forbid")


class UserCreate(_Strict):
    """Registro público. El rol no se acepta aquí: toda cuenta nueva es `user`."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)

    _password_fits = field_validator("password")(_check_password)


class UserUpdate(_Strict):
    """Campos de credenciales; `role` e `is_active` solo los puede cambiar un administrador."""

    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Role | None = None
    is_active: bool | None = None

    _password_fits = field_validator("password")(_check_optional_password)

    @model_validator(mode="after")
    def require_a_field(self) -> "UserUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class ProfileUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def require_a_field(self) -> "ProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class ProfileRead(BaseModel):
    id: str
    user_id: str
    name: str
    phone: str
    address: str


class UserRead(BaseModel):
    """Vista pública de una cuenta: nunca incluye el hash de la contraseña."""

    id: str
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserWithProfile(UserRead):
    profile: ProfileRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class SessionUser(BaseModel):
    """Datos mínimos que el frontend muestra tras iniciar sesión."""

    id: str
    email: str
    name: str
    role: Role


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: SessionUser
