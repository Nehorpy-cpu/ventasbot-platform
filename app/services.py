"""Servicios de dominio transaccionales."""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import (
    AuditLog,
    Customer,
    Delivery,
    DeliveryStatus,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    Role,
    Tenant,
    TenantStatus,
    User,
)
from .schemas import OrderCreate, TenantCreate


def audit(db: Session, *, user: User | None, tenant_id: str | None, action: str,
          entity_type: str, entity_id: str | None, details: dict | None = None) -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        tenant_id=tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=json.dumps(details or {}, ensure_ascii=False),
    ))


def create_tenant(db: Session, payload: TenantCreate, actor: User) -> Tenant:
    tenant = Tenant(
        name=payload.name,
        slug=payload.slug,
        status=TenantStatus.ACTIVE if payload.is_demo else TenantStatus.ONBOARDING,
        is_demo=payload.is_demo,
    )
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        email=payload.owner_email.strip().lower(),
        name=payload.owner_name,
        password_hash=hash_password(payload.owner_password),
        role=Role.TENANT_OWNER,
    )
    db.add(owner)
    audit(db, user=actor, tenant_id=tenant.id, action="tenant.created",
          entity_type="tenant", entity_id=tenant.id, details={"demo": payload.is_demo})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Slug o email ya utilizado") from exc
    db.refresh(tenant)
    return tenant


def upsert_customer(db: Session, tenant_id: str, payload) -> Customer:
    customer = db.scalar(select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.phone == payload.phone,
    ))
    values = payload.model_dump()
    if customer:
        for key, value in values.items():
            if value not in (None, ""):
                setattr(customer, key, value)
        return customer
    customer = Customer(tenant_id=tenant_id, **values)
    db.add(customer)
    db.flush()
    return customer


def create_order(db: Session, tenant_id: str, payload: OrderCreate, actor: User) -> Order:
    product_ids = {item.product_id for item in payload.items}
    products = db.scalars(select(Product).where(
        Product.tenant_id == tenant_id,
        Product.id.in_(product_ids),
        Product.active.is_(True),
    )).all()
    by_id = {p.id: p for p in products}
    if set(by_id) != product_ids:
        raise HTTPException(status_code=400, detail="Uno o más productos no existen o están inactivos")

    requested: dict[str, int] = {}
    for item in payload.items:
        requested[item.product_id] = requested.get(item.product_id, 0) + item.quantity
    for product_id, quantity in requested.items():
        if by_id[product_id].stock < quantity:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente para {by_id[product_id].name}")

    customer = upsert_customer(db, tenant_id, payload.customer)
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        status=OrderStatus.PENDING_CONFIRMATION,
        discount=payload.discount,
        shipping=payload.shipping,
        payment_method=payload.payment_method,
        address=payload.address or payload.customer.address,
        latitude=payload.latitude or payload.customer.latitude,
        longitude=payload.longitude or payload.customer.longitude,
        requested_slot=payload.requested_slot,
        notes=payload.notes,
        source=payload.source,
    )
    subtotal = 0
    for product_id, quantity in requested.items():
        product = by_id[product_id]
        line_total = product.price * quantity
        subtotal += line_total
        order.items.append(OrderItem(
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            subtotal=line_total,
        ))
    if payload.discount > subtotal + payload.shipping:
        raise HTTPException(status_code=400, detail="El descuento supera el total")
    order.subtotal = subtotal
    order.total = subtotal - payload.discount + payload.shipping
    db.add(order)
    db.flush()
    audit(db, user=actor, tenant_id=tenant_id, action="order.created",
          entity_type="order", entity_id=order.id, details={"total": order.total})
    db.commit()
    db.refresh(order)
    return order


ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.PENDING_CONFIRMATION, OrderStatus.CANCELLED},
    OrderStatus.PENDING_CONFIRMATION: {OrderStatus.PENDING_PAYMENT, OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.PENDING_PAYMENT: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY, OrderStatus.CANCELLED},
    OrderStatus.READY: {OrderStatus.ASSIGNED, OrderStatus.CANCELLED},
    OrderStatus.ASSIGNED: {OrderStatus.IN_TRANSIT, OrderStatus.CANCELLED},
    OrderStatus.IN_TRANSIT: {OrderStatus.DELIVERED, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

# Estados en los que el stock del pedido YA se descontó del catálogo. Si un
# pedido en cualquiera de estos se cancela, hay que devolver las unidades.
ESTADOS_CON_STOCK_DESCONTADO = {
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
    OrderStatus.ASSIGNED,
    OrderStatus.IN_TRANSIT,
}


def _productos_del_pedido(db: Session, order: Order) -> dict[str, Product]:
    """Trae los productos del pedido bloqueando la fila.

    with_for_update() evita que dos confirmaciones simultáneas lean el mismo
    stock y lo descuenten dos veces. En SQLite el dialecto lo ignora (no hay
    concurrencia real); en PostgreSQL es lo que sostiene la invariante.
    """
    ids = {item.product_id for item in order.items}
    if not ids:
        return {}
    filas = db.scalars(select(Product).where(Product.id.in_(ids)).with_for_update()).all()
    return {p.id: p for p in filas}


def _descontar_stock(db: Session, order: Order) -> None:
    productos = _productos_del_pedido(db, order)
    for item in order.items:
        producto = productos.get(item.product_id)
        if not producto or producto.tenant_id != order.tenant_id or producto.stock < item.quantity:
            raise HTTPException(status_code=409, detail=f"Stock cambió para {item.product_name}")
    for item in order.items:
        productos[item.product_id].stock -= item.quantity


def _reponer_stock(db: Session, order: Order) -> None:
    productos = _productos_del_pedido(db, order)
    for item in order.items:
        producto = productos.get(item.product_id)
        if producto and producto.tenant_id == order.tenant_id:
            producto.stock += item.quantity


def aplicar_transicion(db: Session, order: Order, target: OrderStatus, actor: User) -> None:
    """Valida la transición y mueve el stock, SIN commitear.

    Separado de change_order_status para que quien ya está dentro de una
    transacción (por ejemplo register_payment) no dispare un commit a la
    mitad y deje el resto de su trabajo sin guardar.
    """
    if target not in ALLOWED_ORDER_TRANSITIONS[order.status]:
        raise HTTPException(status_code=409, detail=f"Transición inválida: {order.status.value} → {target.value}")
    previous = order.status
    if target == OrderStatus.CONFIRMED:
        _descontar_stock(db, order)
    elif target == OrderStatus.CANCELLED and previous in ESTADOS_CON_STOCK_DESCONTADO:
        _reponer_stock(db, order)
    order.status = target
    audit(db, user=actor, tenant_id=order.tenant_id, action="order.status_changed",
          entity_type="order", entity_id=order.id,
          details={"from": previous.value, "to": target.value})


def change_order_status(db: Session, order: Order, target: OrderStatus, actor: User) -> Order:
    aplicar_transicion(db, order, target, actor)
    db.commit()
    db.refresh(order)
    return order


# Un pago RECHAZADO o DEVUELTO no ocupa saldo del pedido.
ESTADOS_DE_PAGO_QUE_SUMAN = {PaymentStatus.PENDING, PaymentStatus.APPROVED}


def _suma_pagos(db: Session, order: Order, estados: set[PaymentStatus]) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.order_id == order.id,
            Payment.status.in_(estados),
        )
    )
    return int(total or 0)


def saldo_pendiente(db: Session, order: Order) -> int:
    """Cuánto falta cobrar del pedido, contando pagos pendientes y aprobados."""
    return order.total - _suma_pagos(db, order, ESTADOS_DE_PAGO_QUE_SUMAN)


def register_payment(db: Session, order: Order, payload, actor: User) -> Payment:
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.order_id != order.id or existing.amount != payload.amount:
            raise HTTPException(status_code=409, detail="Clave de idempotencia reutilizada con otros datos")
        return existing

    # Mirar solo el pago actual dejaba pasar cobros parciales que sumados
    # superaban el total del pedido (dos de 800 sobre un pedido de 1000).
    saldo = saldo_pendiente(db, order)
    if payload.amount > saldo:
        raise HTTPException(status_code=400, detail=f"El pago excede el saldo del pedido (saldo: {saldo})")

    payment = Payment(tenant_id=order.tenant_id, order_id=order.id, **payload.model_dump())
    db.add(payment)
    # Se confirma solo cuando lo efectivamente APROBADO cubre el total: un
    # pago PENDING no alcanza, y dos parciales aprobados sí.
    aprobado = _suma_pagos(db, order, {PaymentStatus.APPROVED})
    if payload.status == PaymentStatus.APPROVED:
        aprobado += payload.amount
    if aprobado >= order.total and order.status in {OrderStatus.PENDING_CONFIRMATION, OrderStatus.PENDING_PAYMENT}:
        aplicar_transicion(db, order, OrderStatus.CONFIRMED, actor)
    audit(db, user=actor, tenant_id=order.tenant_id, action="payment.registered",
          entity_type="payment", entity_id=payment.id,
          details={"provider": payload.provider, "status": payload.status.value})
    db.commit()
    db.refresh(payment)
    return payment


def ensure_delivery(db: Session, order: Order) -> Delivery:
    delivery = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if not delivery:
        delivery = Delivery(tenant_id=order.tenant_id, order_id=order.id)
        db.add(delivery)
        db.flush()
    return delivery


DELIVERY_TO_ORDER = {
    DeliveryStatus.ASSIGNED: OrderStatus.ASSIGNED,
    DeliveryStatus.PICKED_UP: OrderStatus.IN_TRANSIT,
    DeliveryStatus.IN_TRANSIT: OrderStatus.IN_TRANSIT,
    DeliveryStatus.ARRIVED: OrderStatus.IN_TRANSIT,
    DeliveryStatus.DELIVERED: OrderStatus.DELIVERED,
}
