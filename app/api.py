"""API del panel, superadmin, comercio, pagos y delivery."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import (
    assert_tenant_access,
    create_token,
    current_user,
    hash_password,
    require_platform_admin,
    verify_password,
)
from .database import get_db
from .models import (
    Delivery,
    DeliveryStatus,
    Order,
    OrderStatus,
    Payment,
    Product,
    Role,
    Tenant,
    TenantStatus,
    User,
)
from .schemas import (
    DeliveryAssign,
    DeliveryOutput,
    DeliveryUpdate,
    LoginInput,
    OrderCreate,
    OrderOutput,
    PaymentCreate,
    PaymentOutput,
    ProductCreate,
    ProductOutput,
    StatusChange,
    TenantCreate,
    TenantOutput,
    TokenOutput,
    UserCreate,
    UserOutput,
)
from .services import DELIVERY_TO_ORDER, audit, change_order_status, create_order, create_tenant, ensure_delivery, register_payment

router = APIRouter(prefix="/api")
log = logging.getLogger("ventasbot.auth")

# Fuerza bruta: ventana deslizante en memoria. Alcanza para un proceso único;
# si mañana hay varios workers, esto se muda a Redis (ver README).
MAX_INTENTOS = 10
VENTANA_INTENTOS = timedelta(minutes=5)
_intentos: dict[str, list[datetime]] = defaultdict(list)

# Hash señuelo: verificarlo cuando el email no existe hace que la respuesta
# tarde lo mismo que con un email real, así no se puede enumerar usuarios
# midiendo el tiempo de respuesta.
_HASH_SEÑUELO = hash_password("señuelo-para-igualar-tiempos")


def _clave_intentos(payload: LoginInput) -> str:
    return f"{(payload.tenant_slug or '').strip().lower()}|{payload.email.strip().lower()}"


def _registrar_fallo(clave: str) -> None:
    _intentos[clave].append(datetime.now(timezone.utc))


def _bloqueado(clave: str) -> bool:
    corte = datetime.now(timezone.utc) - VENTANA_INTENTOS
    recientes = [t for t in _intentos[clave] if t > corte]
    _intentos[clave] = recientes
    return len(recientes) >= MAX_INTENTOS


@router.post("/auth/login", response_model=TokenOutput)
def login(payload: LoginInput, db: Session = Depends(get_db)):
    clave = _clave_intentos(payload)
    if _bloqueado(clave):
        log.warning("Login bloqueado por exceso de intentos: %s", clave)
        raise HTTPException(status_code=429, detail="Demasiados intentos fallidos, esperá unos minutos")

    query = select(User).where(User.email == payload.email.strip().lower(), User.active.is_(True))
    tenant = None
    if payload.tenant_slug:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == payload.tenant_slug))
        query = query.where(User.tenant_id == (tenant.id if tenant else "__inexistente__"))
    else:
        query = query.where(User.tenant_id.is_(None))
    candidates = db.scalars(query).all()
    user = next((item for item in candidates if verify_password(payload.password, item.password_hash)), None)
    if not user:
        if not candidates:
            verify_password(payload.password, _HASH_SEÑUELO)
        _registrar_fallo(clave)
        log.warning("Credenciales inválidas para %s", clave)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    if tenant and tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(status_code=403, detail="Empresa suspendida")
    _intentos.pop(clave, None)
    return TokenOutput(access_token=create_token(user))


@router.get("/me", response_model=UserOutput)
def me(user: User = Depends(current_user)):
    return user


@router.get("/platform/tenants", response_model=list[TenantOutput])
def list_tenants(_: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()


@router.post("/platform/tenants", response_model=TenantOutput, status_code=201)
def tenant_create(payload: TenantCreate, actor: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    return create_tenant(db, payload, actor)


@router.get("/platform/summary")
def platform_summary(_: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    return {
        "tenants": db.scalar(select(func.count()).select_from(Tenant)),
        "users": db.scalar(select(func.count()).select_from(User)),
        "orders": db.scalar(select(func.count()).select_from(Order)),
    }


@router.post("/tenants/{tenant_id}/users", response_model=UserOutput, status_code=201)
def user_create(tenant_id: str, payload: UserCreate, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    if payload.role in {Role.PLATFORM_ADMIN, Role.TENANT_OWNER} and actor.role != Role.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="No puede crear ese rol")
    user = User(tenant_id=tenant_id, email=payload.email.strip().lower(), name=payload.name,
                password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    audit(db, user=actor, tenant_id=tenant_id, action="user.created", entity_type="user", entity_id=user.id,
          details={"role": payload.role.value})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email ya utilizado en la empresa") from exc
    db.refresh(user)
    return user


@router.get("/tenants/{tenant_id}/products", response_model=list[ProductOutput])
def product_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db),
                 limite: int = Query(100, ge=1, le=500), desde: int = Query(0, ge=0)):
    assert_tenant_access(db, actor, tenant_id)
    return db.scalars(select(Product).where(Product.tenant_id == tenant_id)
                      .order_by(Product.name).offset(desde).limit(limite)).all()


@router.post("/tenants/{tenant_id}/products", response_model=ProductOutput, status_code=201)
def product_create(tenant_id: str, payload: ProductCreate, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.WAREHOUSE})
    product = Product(tenant_id=tenant_id, **payload.model_dump())
    db.add(product)
    audit(db, user=actor, tenant_id=tenant_id, action="product.created", entity_type="product", entity_id=product.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="SKU ya utilizado") from exc
    db.refresh(product)
    return product


@router.post("/tenants/{tenant_id}/orders", response_model=OrderOutput, status_code=201)
def order_create(tenant_id: str, payload: OrderCreate, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    return create_order(db, tenant_id, payload, actor)


def get_order(db: Session, tenant_id: str, order_id: str) -> Order:
    order = db.scalar(select(Order).options(selectinload(Order.items)).where(
        Order.id == order_id, Order.tenant_id == tenant_id))
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return order


@router.get("/tenants/{tenant_id}/orders", response_model=list[OrderOutput])
def order_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db),
               estado: OrderStatus | None = None,
               limite: int = Query(100, ge=1, le=500), desde: int = Query(0, ge=0)):
    assert_tenant_access(db, actor, tenant_id)
    consulta = select(Order).options(selectinload(Order.items)).where(Order.tenant_id == tenant_id)
    if estado:
        consulta = consulta.where(Order.status == estado)
    return db.scalars(consulta.order_by(Order.created_at.desc()).offset(desde).limit(limite)).all()


@router.post("/tenants/{tenant_id}/orders/{order_id}/status", response_model=OrderOutput)
def order_status(tenant_id: str, order_id: str, payload: StatusChange,
                 actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER, Role.WAREHOUSE, Role.DISPATCHER})
    return change_order_status(db, get_order(db, tenant_id, order_id), payload.status, actor)


@router.post("/tenants/{tenant_id}/orders/{order_id}/payments", response_model=PaymentOutput, status_code=201)
def payment_create(tenant_id: str, order_id: str, payload: PaymentCreate,
                   actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    return register_payment(db, get_order(db, tenant_id, order_id), payload, actor)


@router.post("/tenants/{tenant_id}/orders/{order_id}/delivery/assign", response_model=DeliveryOutput)
def delivery_assign(tenant_id: str, order_id: str, payload: DeliveryAssign,
                    actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.DISPATCHER})
    order = get_order(db, tenant_id, order_id)
    if order.status != OrderStatus.READY:
        raise HTTPException(status_code=409, detail="El pedido debe estar READY")
    driver = db.get(User, payload.driver_id)
    if not driver or driver.tenant_id != tenant_id or driver.role != Role.DRIVER or not driver.active:
        raise HTTPException(status_code=400, detail="Delivery inválido")
    delivery = ensure_delivery(db, order)
    delivery.driver_id = driver.id
    delivery.status = DeliveryStatus.ASSIGNED
    order.status = OrderStatus.ASSIGNED
    audit(db, user=actor, tenant_id=tenant_id, action="delivery.assigned", entity_type="delivery",
          entity_id=delivery.id, details={"driver_id": driver.id})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/tenants/{tenant_id}/deliveries/{delivery_id}/status", response_model=DeliveryOutput)
def delivery_update(tenant_id: str, delivery_id: str, payload: DeliveryUpdate,
                    actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.DISPATCHER, Role.DRIVER})
    delivery = db.scalar(select(Delivery).where(Delivery.id == delivery_id, Delivery.tenant_id == tenant_id))
    if not delivery:
        raise HTTPException(status_code=404, detail="Entrega no encontrada")
    if actor.role == Role.DRIVER and delivery.driver_id != actor.id:
        raise HTTPException(status_code=403, detail="Entrega asignada a otro delivery")
    delivery.status = payload.status
    # Solo se pisa lo que vino: mandar el estado sin coordenadas no debe
    # borrar la ultima posicion reportada por el repartidor.
    if payload.latitude is not None:
        delivery.current_latitude = payload.latitude
    if payload.longitude is not None:
        delivery.current_longitude = payload.longitude
    if payload.proof_note is not None:
        delivery.proof_note = payload.proof_note
    order = get_order(db, tenant_id, delivery.order_id)
    mapped = DELIVERY_TO_ORDER.get(payload.status)
    if mapped:
        order.status = mapped
    audit(db, user=actor, tenant_id=tenant_id, action="delivery.status_changed", entity_type="delivery",
          entity_id=delivery.id, details={"status": payload.status.value})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.get("/tracking/{tracking_token}")
def public_tracking(tracking_token: str, db: Session = Depends(get_db)):
    delivery = db.scalar(select(Delivery).where(Delivery.tracking_token == tracking_token))
    if not delivery:
        raise HTTPException(status_code=404, detail="Tracking no encontrado")
    order = db.get(Order, delivery.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Tracking no encontrado")
    return {
        "order_id": order.id,
        "order_status": order.status.value,
        "delivery_status": delivery.status.value,
        "latitude": delivery.current_latitude,
        "longitude": delivery.current_longitude,
        "updated_at": delivery.updated_at,
    }
