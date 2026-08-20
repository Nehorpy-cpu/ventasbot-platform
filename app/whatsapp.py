"""Cuentas de WhatsApp por empresa: alta, resolución y verificación.

Cómo funciona el ruteo multiempresa:

- Una sola App de Meta (la de la plataforma) recibe TODOS los webhooks. Por eso
  APP_SECRET y VERIFY_TOKEN son globales y siguen viviendo en .env.
- Cada empresa carga su propio número: `phone_number_id` + `access_token`.
- Meta incluye en cada payload `value.metadata.phone_number_id`, que dice a qué
  número llegó el mensaje. Con eso se resuelve la empresa dueña.
- Si ningún tenant reclama ese número, el mensaje se descarta con un warning:
  responderle a alguien con las credenciales de otra empresa sería peor.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cripto import cifrar, descifrar
from .mensajes import Credenciales
from .models import Tenant, TenantStatus, WhatsAppAccount, utcnow

log = logging.getLogger("whatsapp.cuentas")


def credenciales_de_cuenta(cuenta: WhatsAppAccount, graph_version: str) -> Credenciales:
    return Credenciales(
        phone_number_id=cuenta.phone_number_id,
        access_token=descifrar(cuenta.access_token_cifrado),
        graph_version=graph_version,
    )


def cuenta_por_numero(db: Session, phone_number_id: str) -> WhatsAppAccount | None:
    """La cuenta activa dueña de ese número, si la empresa está operativa."""
    if not phone_number_id:
        return None
    cuenta = db.scalar(
        select(WhatsAppAccount).where(
            WhatsAppAccount.phone_number_id == phone_number_id,
            WhatsAppAccount.active.is_(True),
        )
    )
    if not cuenta:
        return None
    tenant = db.get(Tenant, cuenta.tenant_id)
    if not tenant or tenant.status == TenantStatus.SUSPENDED:
        log.warning("Mensaje para %s: la empresa está suspendida o no existe", phone_number_id)
        return None
    return cuenta


def cuenta_de_tenant(db: Session, tenant_id: str) -> WhatsAppAccount | None:
    return db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant_id))


def guardar_cuenta(db: Session, tenant_id: str, payload) -> WhatsAppAccount:
    """Alta o actualización del número de una empresa.

    El token se guarda cifrado y nunca vuelve por la API. Si la empresa manda
    el formulario sin token (porque solo quiere corregir el número visible),
    se conserva el que ya estaba.
    """
    duenio = db.scalar(
        select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id == payload.phone_number_id)
    )
    if duenio and duenio.tenant_id != tenant_id:
        raise HTTPException(status_code=409, detail="Ese número ya está cargado por otra empresa")

    cuenta = cuenta_de_tenant(db, tenant_id)
    if cuenta is None:
        if not payload.access_token:
            raise HTTPException(status_code=400, detail="Falta el access_token para dar de alta el número")
        cuenta = WhatsAppAccount(
            tenant_id=tenant_id,
            phone_number_id=payload.phone_number_id,
            access_token_cifrado=cifrar(payload.access_token),
        )
        db.add(cuenta)
    else:
        cuenta.phone_number_id = payload.phone_number_id
        if payload.access_token:
            cuenta.access_token_cifrado = cifrar(payload.access_token)
            # Token nuevo: la verificación anterior ya no dice nada.
            cuenta.verificado_en = None

    cuenta.display_phone_number = payload.display_phone_number or cuenta.display_phone_number or ""
    cuenta.waba_id = payload.waba_id or cuenta.waba_id
    if payload.active is not None:
        cuenta.active = payload.active
    db.flush()
    return cuenta


def marcar_verificada(cuenta: WhatsAppAccount, display_phone_number: str) -> None:
    cuenta.verificado_en = utcnow()
    if display_phone_number:
        cuenta.display_phone_number = display_phone_number
