"""Crea o actualiza el superadmin inicial sin imprimir credenciales."""

from __future__ import annotations

import os

from sqlalchemy import select

from .auth import hash_password
from .database import SessionLocal, create_schema
from .models import Role, User


def main() -> None:
    email = os.getenv("SUPERADMIN_EMAIL", "admin@ventasbot.com").strip().lower()
    password = os.getenv("SUPERADMIN_PASSWORD", "")
    if len(password) < 8 or password == "cambiar-esta-clave":
        raise SystemExit("Definí SUPERADMIN_PASSWORD con al menos 8 caracteres y sin usar el placeholder")
    create_schema()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.tenant_id.is_(None), User.email == email))
        if user:
            user.password_hash = hash_password(password)
            user.active = True
            user.role = Role.PLATFORM_ADMIN
        else:
            user = User(tenant_id=None, email=email, name="Platform Admin",
                        password_hash=hash_password(password), role=Role.PLATFORM_ADMIN)
            db.add(user)
        db.commit()
    print(f"Superadmin listo: {email}")


if __name__ == "__main__":
    main()
