"""Callback de Bancard Tpago con allowlist e idempotencia."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Order, OrderStatus, Payment, PaymentStatus
from .services import change_order_status

router = APIRouter(prefix="/webhooks/payments", tags=["Payments"])


def _allowed_callback_ips() -> set[str]:
    return {value.strip() for value in os.getenv(
        "BANCARD_CALLBACK_IPS", "190.128.218.209,190.128.232.10,190.104.129.98,200.85.46.226"
    ).split(",") if value.strip()}


@router.post("/bancard/{tenant_id}")
async def bancard_callback(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    source_ip = request.client.host if request.client else ""
    if source_ip not in _allowed_callback_ips():
        raise HTTPException(status_code=403, detail="Origen Bancard no permitido")
    body = await request.json()
    event = body.get("payment") or {}
    alias = str(event.get("link_alias") or "")
    payment = db.scalar(select(Payment).where(
        Payment.tenant_id == tenant_id,
        Payment.provider == "BANCARD",
        Payment.external_id == alias,
    ))
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    try:
        amount = int(event.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Monto inválido")
    if amount != payment.amount:
        raise HTTPException(status_code=409, detail="Monto no coincide")
    status = str(event.get("status") or "").lower()
    response_code = str(event.get("response_code") or "")
    order = db.scalar(select(Order).where(Order.id == payment.order_id, Order.tenant_id == tenant_id))
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if status == "confirmed" and response_code == "00":
        if payment.status != PaymentStatus.APPROVED:
            payment.status = PaymentStatus.APPROVED
            payment.external_id = alias
            if order.status in {OrderStatus.PENDING_CONFIRMATION, OrderStatus.PENDING_PAYMENT}:
                change_order_status(db, order, OrderStatus.CONFIRMED, None)
    elif status in {"reversed", "reverse pending"}:
        payment.status = PaymentStatus.REFUNDED
    elif status in {"failed", "reverse failed"}:
        payment.status = PaymentStatus.REJECTED
    db.commit()
    return {"status": "success"}
