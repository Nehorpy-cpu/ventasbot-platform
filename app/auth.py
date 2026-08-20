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


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(user: User) -> str:
    secret = os.getenv("JWT_SECRET", "dev-only-change-this-secret-before-production")
    minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "tenant_id": user.tenant_id,
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    secret = os.getenv("JWT_SECRET", "dev-only-change-this-secret-before-production")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        user_id = payload.get("sub")
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido") from exc
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
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
