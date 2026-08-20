"""Autenticación JWT y autorización multiempresa."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Role, Tenant, TenantStatus, User

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Valores de ejemplo que nunca deben terminar firmando tokens de verdad.
_SECRETOS_PROHIBIDOS = {
    "cambiar-por-un-secreto-aleatorio-de-al-menos-32-caracteres",
    "dev-only-change-this-secret-before-production",
}


def jwt_secret() -> str:
    """Secreto de firma. Explota al arrancar si no está bien configurado.

    Antes había un default hardcodeado: si JWT_SECRET faltaba en producción,
    cualquiera que leyera el repo podía firmarse un token de PLATFORM_ADMIN.
    Preferimos que el login falle ruidosamente a que la app quede abierta.
    """
    secreto = os.getenv("JWT_SECRET", "").strip()
    if len(secreto) < 32 or secreto in _SECRETOS_PROHIBIDOS:
        raise RuntimeError(
            "JWT_SECRET sin configurar: definí un valor aleatorio de al menos "
            "32 caracteres en .env (no el placeholder de .env.example)."
        )
    return secreto


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(user: User) -> str:
    minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "tenant_id": user.tenant_id,
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(
            token,
            jwt_secret(),
            algorithms=["HS256"],
            # Sin `require` un token sin `exp` se aceptaría para siempre.
            options={"require": ["exp", "sub"]},
        )
        user_id = payload.get("sub")
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido") from exc
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
    # El rol viaja en el token pero manda la base: si a alguien lo degradaron o
    # lo movieron de empresa, su token viejo no debe conservar los permisos.
    if payload.get("role") != user.role.value or payload.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token desactualizado")
    return user


def require_platform_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="Requiere PLATFORM_ADMIN")
    return user


def assert_tenant_access(db: Session, user: User, tenant_id: str, roles: set[Role] | None = None) -> Tenant:
    if user.role != Role.PLATFORM_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Acceso a empresa denegado")
    if roles and user.role != Role.PLATFORM_ADMIN and user.role not in roles:
        raise HTTPException(status_code=403, detail="Rol insuficiente")
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if tenant.status == TenantStatus.SUSPENDED and user.role != Role.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="Empresa suspendida")
    return tenant
