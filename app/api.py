"""API del panel, superadmin, comercio, pagos y delivery."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import (
    assert_tenant_access,
    check_login_rate_limit,
    create_token,
    current_user,
    hash_password,
    require_platform_admin,
    verify_password,
)
from .database import get_db
from .crm import save_message, send_whatsapp_text
from .models import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    Customer,
    AgentRun,
    AgentRunStatus,
    PendingAgentAction,
    PendingActionStatus,
    SalesObjection,
    SalesPlaybook,
    Delivery,
    DeliveryEvent,
    DeliveryStatus,
    MessageDirection,
    Order,
    OrderStatus,
    Payment,
    PaymentMethodConfig,
    PaymentStatus,
    Invoice,
    InvoiceStatus,
    Product,
    Role,
    Tenant,
    User,
    WhatsappIntegration,
    utcnow,
)
from .schemas import (
    DeliveryAssign,
    DeliveryOutput,
    DeliveryUpdate,
    ConversationAssign,
    MessageOutput,
    SendMessageInput,
    LoginInput,
    OrderCreate,
    OrderOutput,
    PaymentCreate,
    PaymentOutput,
    PaymentMethodConfigInput,
    PaymentMethodConfigOutput,
    CheckoutInput,
    CheckoutOutput,
    CatalogImportInput,
    CatalogImportOutput,
    InvoiceIssueInput,
    InvoiceOutput,
    ProductCreate,
    ProductOutput,
    StatusChange,
    TenantCreate,
    TenantOutput,
    TokenOutput,
    UserCreate,
    UserOutput,
    WhatsappIntegrationInput,
    WhatsappIntegrationOutput,
    SalesPlaybookInput,
    SalesPlaybookOutput,
    PendingActionResolveInput,
)
from .sales_agent import default_playbook, split_terms
from .payment_gateway import create_bancard_checkout
from .services import (
    ALLOWED_DELIVERY_TRANSITIONS,
    DELIVERY_TO_ORDER,
    add_delivery_event,
    audit,
    change_order_status,
    create_order,
    create_tenant,
    ensure_delivery,
    register_payment,
)

router = APIRouter(prefix="/api")


def _playbook_output(db: Session, playbook: SalesPlaybook) -> dict:
    objections = db.scalars(select(SalesObjection).where(
        SalesObjection.tenant_id == playbook.tenant_id
    ).order_by(SalesObjection.name)).all()
    return {
        "id": playbook.id, "tenant_id": playbook.tenant_id, "enabled": playbook.enabled,
        "mode": playbook.mode, "brand_tone": playbook.brand_tone,
        "hot_threshold": playbook.hot_threshold, "warm_threshold": playbook.warm_threshold,
        "auto_send_min_confidence": playbook.auto_send_min_confidence,
        "escalation_words": split_terms(playbook.escalation_words),
        "objections": [{
            "id": row.id, "tenant_id": row.tenant_id, "name": row.name,
            "triggers": split_terms(row.triggers), "response": row.response, "active": row.active,
        } for row in objections],
    }


def _tenant_secret_prefix(tenant_id: str) -> str:
    return "TENANT_" + "".join(character if character.isalnum() else "_" for character in tenant_id.upper()) + "_"


def _require_scoped_secret_name(tenant_id: str, name: str, label: str) -> None:
    if name and not name.startswith(_tenant_secret_prefix(tenant_id)):
        raise HTTPException(status_code=400, detail=f"{label} debe comenzar con {_tenant_secret_prefix(tenant_id)}")


@router.post("/auth/login", response_model=TokenOutput)
def login(payload: LoginInput, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{payload.email.strip().lower()}"
    check_login_rate_limit(rate_key)
    query = select(User).where(User.email == payload.email.strip().lower(), User.active.is_(True))
    if payload.tenant_slug:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == payload.tenant_slug))
        if not tenant:
            check_login_rate_limit(rate_key, failed=True)
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
        query = query.where(User.tenant_id == tenant.id)
    else:
        query = query.where(User.tenant_id.is_(None))
    candidates = db.scalars(query).all()
    user = next((item for item in candidates if verify_password(payload.password, item.password_hash)), None)
    if not user:
        check_login_rate_limit(rate_key, failed=True)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    check_login_rate_limit(rate_key, failed=False)
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
    user = User(tenant_id=tenant_id, email=payload.email.strip().lower(), name=payload.name, phone=payload.phone,
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


@router.get("/tenants/{tenant_id}/users", response_model=list[UserOutput])
def user_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    return db.scalars(select(User).where(
        User.tenant_id == tenant_id, User.active.is_(True)
    ).order_by(User.name)).all()


@router.get("/tenants/{tenant_id}/products", response_model=list[ProductOutput])
def product_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    return db.scalars(select(Product).where(Product.tenant_id == tenant_id).order_by(Product.name)).all()


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


@router.post("/tenants/{tenant_id}/catalog/import", response_model=CatalogImportOutput)
def catalog_import(tenant_id: str, payload: CatalogImportInput,
                   actor: User = Depends(current_user), db: Session = Depends(get_db)):
    """Contrato común para conectores Meta, web, CSV o WhatsApp Business."""
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.WAREHOUSE})
    incoming = {item.sku: item for item in payload.products}
    if len(incoming) != len(payload.products):
        raise HTTPException(status_code=400, detail="El lote contiene SKU duplicados")
    existing = db.scalars(select(Product).where(
        Product.tenant_id == tenant_id, Product.sku.in_(incoming)
    ).with_for_update()).all()
    by_sku = {product.sku: product for product in existing}
    created = updated = deactivated = 0
    for sku, item in incoming.items():
        values = item.model_dump()
        product = by_sku.get(sku)
        if product:
            for key, value in values.items():
                setattr(product, key, value)
            updated += 1
        else:
            db.add(Product(tenant_id=tenant_id, **values))
            created += 1
    if payload.deactivate_missing:
        missing = db.scalars(select(Product).where(
            Product.tenant_id == tenant_id,
            Product.sku.not_in(incoming),
            Product.active.is_(True),
        ).with_for_update()).all()
        for product in missing:
            product.active = False
            deactivated += 1
    audit(db, user=actor, tenant_id=tenant_id, action="catalog.imported", entity_type="catalog",
          entity_id=None, details={"source": payload.source, "created": created,
                                  "updated": updated, "deactivated": deactivated})
    db.commit()
    return CatalogImportOutput(source=payload.source, created=created, updated=updated,
                               deactivated=deactivated, total_received=len(payload.products))


@router.get("/tenants/{tenant_id}/integrations/whatsapp", response_model=WhatsappIntegrationOutput | None)
def whatsapp_get(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    return db.scalar(select(WhatsappIntegration).where(WhatsappIntegration.tenant_id == tenant_id))


@router.put("/tenants/{tenant_id}/integrations/whatsapp", response_model=WhatsappIntegrationOutput)
def whatsapp_put(tenant_id: str, payload: WhatsappIntegrationInput,
                 actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    _require_scoped_secret_name(tenant_id, payload.access_token_env, "access_token_env")
    if payload.active and not payload.access_token_env:
        raise HTTPException(status_code=400, detail="Una integración activa requiere access_token_env")
    integration = db.scalar(select(WhatsappIntegration).where(WhatsappIntegration.tenant_id == tenant_id))
    if integration:
        for key, value in payload.model_dump().items():
            setattr(integration, key, value)
    else:
        integration = WhatsappIntegration(tenant_id=tenant_id, **payload.model_dump())
        db.add(integration)
    audit(db, user=actor, tenant_id=tenant_id, action="whatsapp.configured",
          entity_type="whatsapp_integration", entity_id=integration.id,
          details={"phone_number_id": payload.phone_number_id, "active": payload.active})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El phone_number_id ya pertenece a otra empresa") from exc
    db.refresh(integration)
    return integration


@router.get("/tenants/{tenant_id}/conversations")
def conversation_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    rows = db.execute(
        select(Conversation, Customer).join(Customer, Customer.id == Conversation.customer_id).where(
            Conversation.tenant_id == tenant_id
        ).order_by(Conversation.last_message_at.desc())
    ).all()
    return [{
        "id": conversation.id,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "customer_phone": customer.phone,
        "status": conversation.status.value,
        "assigned_user_id": conversation.assigned_user_id,
        "bot_state": conversation.bot_state,
        "unread_count": conversation.unread_count,
        "last_message_at": conversation.last_message_at,
    } for conversation, customer in rows]


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/messages", response_model=list[MessageOutput])
def message_list(tenant_id: str, conversation_id: str,
                 actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    conversation = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    conversation.unread_count = 0
    messages = db.scalars(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id,
        ConversationMessage.tenant_id == tenant_id,
    ).order_by(ConversationMessage.created_at)).all()
    db.commit()
    return messages


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/assign")
def conversation_assign(tenant_id: str, conversation_id: str, payload: ConversationAssign,
                        actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    conversation = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    if payload.assigned_user_id:
        assignee = db.get(User, payload.assigned_user_id)
        if not assignee or assignee.tenant_id != tenant_id or not assignee.active:
            raise HTTPException(status_code=400, detail="Usuario asignado inválido")
    conversation.assigned_user_id = payload.assigned_user_id
    conversation.status = payload.status
    audit(db, user=actor, tenant_id=tenant_id, action="conversation.assigned",
          entity_type="conversation", entity_id=conversation.id,
          details={"status": payload.status.value, "assigned_user_id": payload.assigned_user_id})
    db.commit()
    return {"ok": True, "status": conversation.status.value}


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/messages", response_model=MessageOutput)
async def message_send(tenant_id: str, conversation_id: str, payload: SendMessageInput,
                       actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    row = db.execute(
        select(Conversation, Customer).join(Customer, Customer.id == Conversation.customer_id).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    conversation, customer = row
    integration = db.scalar(select(WhatsappIntegration).where(WhatsappIntegration.tenant_id == tenant_id))
    if not integration:
        raise HTTPException(status_code=409, detail="WhatsApp no configurado")
    try:
        external_id = await send_whatsapp_text(integration, customer.phone, payload.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Meta rechazó el mensaje") from exc
    message = save_message(db, tenant_id=tenant_id, conversation_id=conversation.id,
                           direction=MessageDirection.OUTBOUND, message_type="text",
                           text=payload.text, external_id=external_id)
    conversation.status = ConversationStatus.HUMAN
    conversation.assigned_user_id = actor.id
    conversation.last_message_at = utcnow()
    audit(db, user=actor, tenant_id=tenant_id, action="conversation.message_sent",
          entity_type="conversation", entity_id=conversation.id)
    db.commit()
    db.refresh(message)
    return message


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
def order_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    return db.scalars(select(Order).options(selectinload(Order.items)).where(
        Order.tenant_id == tenant_id).order_by(Order.created_at.desc())).all()


@router.get("/tenants/{tenant_id}/invoices", response_model=list[InvoiceOutput])
def invoice_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    return db.scalars(select(Invoice).where(
        Invoice.tenant_id == tenant_id).order_by(Invoice.created_at.desc())).all()


@router.post("/tenants/{tenant_id}/invoices/{invoice_id}/issue", response_model=InvoiceOutput)
def invoice_issue(tenant_id: str, invoice_id: str, payload: InvoiceIssueInput,
                  actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    invoice = db.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    if invoice.status != InvoiceStatus.PENDING:
        raise HTTPException(status_code=409, detail="La factura ya fue procesada")
    invoice.status = InvoiceStatus.ISSUED
    invoice.document_number = payload.document_number
    invoice.external_id = payload.external_id
    invoice.kude_url = payload.kude_url
    invoice.issued_at = utcnow()
    audit(db, user=actor, tenant_id=tenant_id, action="invoice.issued", entity_type="invoice",
          entity_id=invoice.id, details={"document_number": payload.document_number})
    db.commit()
    db.refresh(invoice)
    return invoice


@router.post("/tenants/{tenant_id}/orders/{order_id}/status", response_model=OrderOutput)
def order_status(tenant_id: str, order_id: str, payload: StatusChange,
                 actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER, Role.WAREHOUSE, Role.DISPATCHER})
    return change_order_status(db, get_order(db, tenant_id, order_id), payload.status, actor)


@router.post("/tenants/{tenant_id}/orders/{order_id}/payments", response_model=PaymentOutput, status_code=201)
def payment_create(tenant_id: str, order_id: str, payload: PaymentCreate,
                   actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    if payload.status == PaymentStatus.APPROVED and actor.role == Role.SELLER:
        raise HTTPException(status_code=403, detail="Un vendedor no puede aprobar pagos")
    if payload.status == PaymentStatus.APPROVED and payload.provider.upper() == "BANCARD":
        raise HTTPException(status_code=409, detail="Bancard debe confirmarse mediante callback verificado")
    return register_payment(db, get_order(db, tenant_id, order_id), payload, actor)


@router.get("/tenants/{tenant_id}/payment-methods", response_model=list[PaymentMethodConfigOutput])
def payment_method_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    return db.scalars(select(PaymentMethodConfig).where(
        PaymentMethodConfig.tenant_id == tenant_id
    ).order_by(PaymentMethodConfig.display_name)).all()


@router.put("/tenants/{tenant_id}/payment-methods/{code}", response_model=PaymentMethodConfigOutput)
def payment_method_put(tenant_id: str, code: str, payload: PaymentMethodConfigInput,
                       actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    if code != payload.code:
        raise HTTPException(status_code=400, detail="El código de la URL y del cuerpo debe coincidir")
    _require_scoped_secret_name(tenant_id, payload.public_key_env, "public_key_env")
    _require_scoped_secret_name(tenant_id, payload.private_key_env, "private_key_env")
    if code == "BANCARD" and payload.enabled and (
        not payload.public_key_env or not payload.private_key_env or
        not payload.commerce_code or not payload.branch_code
    ):
        raise HTTPException(status_code=400, detail="Bancard/Tpago requiere secretos y códigos de comercio/sucursal")
    config = db.scalar(select(PaymentMethodConfig).where(
        PaymentMethodConfig.tenant_id == tenant_id,
        PaymentMethodConfig.code == code,
    ))
    if config:
        for key, value in payload.model_dump().items():
            setattr(config, key, value)
    else:
        config = PaymentMethodConfig(tenant_id=tenant_id, **payload.model_dump())
        db.add(config)
    audit(db, user=actor, tenant_id=tenant_id, action="payment_method.configured",
          entity_type="payment_method", entity_id=config.id,
          details={"code": code, "enabled": payload.enabled})
    db.commit()
    db.refresh(config)
    return config


@router.post("/tenants/{tenant_id}/orders/{order_id}/checkout", response_model=CheckoutOutput)
async def checkout_create(tenant_id: str, order_id: str, payload: CheckoutInput,
                          actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    order = get_order(db, tenant_id, order_id)
    existing = db.scalar(select(Payment).where(
        Payment.tenant_id == tenant_id,
        Payment.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        if existing.order_id != order.id or existing.provider != payload.method:
            raise HTTPException(status_code=409, detail="Clave de idempotencia reutilizada")
        return CheckoutOutput(payment_id=existing.id, method=existing.provider, status=existing.status,
                              checkout_url=existing.checkout_url)
    config = db.scalar(select(PaymentMethodConfig).where(
        PaymentMethodConfig.tenant_id == tenant_id,
        PaymentMethodConfig.code == payload.method,
        PaymentMethodConfig.enabled.is_(True),
    ))
    if not config:
        raise HTTPException(status_code=409, detail="Método de pago no habilitado")
    # Reclama la clave antes de cualquier llamada externa para evitar dos sesiones Bancard concurrentes.
    payment = Payment(tenant_id=tenant_id, order_id=order.id, provider=payload.method,
                      status=PaymentStatus.PENDING, amount=order.total,
                      idempotency_key=payload.idempotency_key)
    db.add(payment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        claimed = db.scalar(select(Payment).where(
            Payment.tenant_id == tenant_id, Payment.idempotency_key == payload.idempotency_key))
        if claimed and claimed.order_id == order.id and claimed.provider == payload.method:
            return CheckoutOutput(payment_id=claimed.id, method=claimed.provider, status=claimed.status,
                                  checkout_url=claimed.checkout_url)
        raise HTTPException(status_code=409, detail="Clave de idempotencia reutilizada")
    checkout_url = None
    external_id = None
    if payload.method == "BANCARD":
        try:
            session = await create_bancard_checkout(config, order)
        except RuntimeError as exc:
            payment.status = PaymentStatus.REJECTED
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            payment.status = PaymentStatus.REJECTED
            db.commit()
            raise HTTPException(status_code=502, detail="No se pudo iniciar Bancard") from exc
        checkout_url = session.checkout_url
        external_id = session.external_id
        if order.status == OrderStatus.PENDING_CONFIRMATION:
            change_order_status(db, order, OrderStatus.PENDING_PAYMENT, actor)
    elif payload.method == "BANK_TRANSFER":
        if order.status == OrderStatus.PENDING_CONFIRMATION:
            change_order_status(db, order, OrderStatus.PENDING_PAYMENT, actor)
    elif payload.method == "CASH_ON_DELIVERY":
        if order.status == OrderStatus.PENDING_CONFIRMATION:
            change_order_status(db, order, OrderStatus.CONFIRMED, actor)
    else:
        raise HTTPException(status_code=400, detail="Adaptador de pago no implementado")
    payment.external_id = external_id
    payment.checkout_url = checkout_url
    audit(db, user=actor, tenant_id=tenant_id, action="checkout.created", entity_type="payment",
          entity_id=payment.id, details={"method": payload.method})
    db.commit()
    db.refresh(payment)
    return CheckoutOutput(payment_id=payment.id, method=payload.method, status=payment.status,
                          checkout_url=checkout_url, instructions=config.instructions)


@router.post("/tenants/{tenant_id}/orders/{order_id}/delivery/assign", response_model=DeliveryOutput)
async def delivery_assign(tenant_id: str, order_id: str, payload: DeliveryAssign,
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
    add_delivery_event(db, delivery, note="Delivery asignado")
    audit(db, user=actor, tenant_id=tenant_id, action="delivery.assigned", entity_type="delivery",
          entity_id=delivery.id, details={"driver_id": driver.id})
    db.commit()
    db.refresh(delivery)
    if driver.phone:
        integration = db.scalar(select(WhatsappIntegration).where(
            WhatsappIntegration.tenant_id == tenant_id, WhatsappIntegration.active.is_(True)))
        customer = db.get(Customer, order.customer_id)
        if integration and customer:
            maps_url = (f"https://www.google.com/maps?q={order.latitude},{order.longitude}"
                        if order.latitude and order.longitude else order.address)
            base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
            tracking_url = f"{base_url}/panel/tracking.html?token={delivery.tracking_token}" if base_url else ""
            message = (f"Nueva entrega #{order.id[-6:]}\nCliente: {customer.name or 'Sin nombre'}\n"
                       f"Contacto: {customer.phone}\nUbicación: {maps_url}\nHorario: {order.requested_slot or 'A coordinar'}")
            if tracking_url:
                message += f"\nTracking: {tracking_url}"
            try:
                await send_whatsapp_text(integration, driver.phone, message)
            except Exception:
                # La asignación es la fuente de verdad; un fallo de Meta queda reintentable por operaciones.
                pass
    return delivery


@router.get("/tenants/{tenant_id}/deliveries")
def delivery_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    query = select(Delivery, Order, Customer).join(Order, Order.id == Delivery.order_id).join(
        Customer, Customer.id == Order.customer_id).where(Delivery.tenant_id == tenant_id)
    if actor.role == Role.DRIVER:
        query = query.where(Delivery.driver_id == actor.id)
    rows = db.execute(query.order_by(Delivery.updated_at.desc())).all()
    return [{
        "id": delivery.id, "order_id": order.id, "driver_id": delivery.driver_id,
        "status": delivery.status.value, "tracking_token": delivery.tracking_token,
        "customer_name": customer.name, "customer_phone": customer.phone,
        "address": order.address, "latitude": order.latitude, "longitude": order.longitude,
        "requested_slot": order.requested_slot, "total": order.total,
    } for delivery, order, customer in rows]


@router.post("/tenants/{tenant_id}/deliveries/{delivery_id}/status", response_model=DeliveryOutput)
def delivery_update(tenant_id: str, delivery_id: str, payload: DeliveryUpdate,
                    actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.DISPATCHER, Role.DRIVER})
    delivery = db.scalar(select(Delivery).where(Delivery.id == delivery_id, Delivery.tenant_id == tenant_id))
    if not delivery:
        raise HTTPException(status_code=404, detail="Entrega no encontrada")
    if actor.role == Role.DRIVER and delivery.driver_id != actor.id:
        raise HTTPException(status_code=403, detail="Entrega asignada a otro delivery")
    if payload.status not in ALLOWED_DELIVERY_TRANSITIONS[delivery.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Transición de entrega inválida: {delivery.status.value} → {payload.status.value}",
        )
    delivery.status = payload.status
    delivery.current_latitude = payload.latitude
    delivery.current_longitude = payload.longitude
    delivery.proof_note = payload.proof_note
    order = get_order(db, tenant_id, delivery.order_id)
    mapped = DELIVERY_TO_ORDER.get(payload.status)
    if mapped:
        order.status = mapped
    add_delivery_event(db, delivery, note=payload.proof_note or "")
    audit(db, user=actor, tenant_id=tenant_id, action="delivery.status_changed", entity_type="delivery",
          entity_id=delivery.id, details={"status": payload.status.value})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.get("/tenants/{tenant_id}/sales/playbook", response_model=SalesPlaybookOutput)
def sales_playbook_get(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id)
    playbook = default_playbook(db, tenant_id)
    db.commit()
    return _playbook_output(db, playbook)


@router.put("/tenants/{tenant_id}/sales/playbook", response_model=SalesPlaybookOutput)
def sales_playbook_update(tenant_id: str, payload: SalesPlaybookInput,
                          actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER})
    if payload.warm_threshold >= payload.hot_threshold:
        raise HTTPException(status_code=400, detail="El umbral tibio debe ser menor que el caliente")
    playbook = default_playbook(db, tenant_id)
    for field in ("enabled", "mode", "brand_tone", "hot_threshold", "warm_threshold",
                  "auto_send_min_confidence"):
        setattr(playbook, field, getattr(payload, field))
    playbook.escalation_words = ",".join(dict.fromkeys(split_terms(",".join(payload.escalation_words))))
    existing = {row.name.lower(): row for row in db.scalars(select(SalesObjection).where(
        SalesObjection.tenant_id == tenant_id)).all()}
    supplied: set[str] = set()
    for item in payload.objections:
        key = item.name.strip().lower()
        supplied.add(key)
        objection = existing.get(key) or SalesObjection(tenant_id=tenant_id, name=item.name.strip())
        objection.name = item.name.strip()
        objection.triggers = ",".join(dict.fromkeys(split_terms(",".join(item.triggers))))
        objection.response = item.response.strip()
        objection.active = item.active
        db.add(objection)
    for key, objection in existing.items():
        if key not in supplied:
            objection.active = False
    audit(db, user=actor, tenant_id=tenant_id, action="sales.playbook_updated",
          entity_type="sales_playbook", entity_id=playbook.id,
          details={"mode": payload.mode.value, "objections": len(payload.objections)})
    db.commit()
    return _playbook_output(db, playbook)


@router.get("/tenants/{tenant_id}/sales/pending")
def sales_pending_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    rows = db.execute(select(PendingAgentAction, AgentRun, Customer).join(
        AgentRun, AgentRun.id == PendingAgentAction.run_id
    ).join(Customer, Customer.id == PendingAgentAction.customer_id).where(
        PendingAgentAction.tenant_id == tenant_id,
        PendingAgentAction.status == PendingActionStatus.PENDING,
    ).order_by(PendingAgentAction.created_at.desc())).all()
    return [{
        "id": pending.id, "run_id": run.id, "conversation_id": pending.conversation_id,
        "customer_id": customer.id, "customer_name": customer.name, "customer_phone": customer.phone,
        "proposed_text": pending.proposed_text, "created_at": pending.created_at,
        "intent": run.intent, "confidence": run.confidence, "lead_score": run.lead_score,
        "temperature": run.temperature, "input_text": run.input_text,
    } for pending, run, customer in rows]


@router.get("/tenants/{tenant_id}/sales/runs")
def sales_run_list(tenant_id: str, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    runs = db.scalars(select(AgentRun).where(AgentRun.tenant_id == tenant_id)
                      .order_by(AgentRun.created_at.desc()).limit(100)).all()
    return runs


@router.post("/tenants/{tenant_id}/sales/pending/{pending_id}/approve")
async def sales_pending_approve(tenant_id: str, pending_id: str, payload: PendingActionResolveInput,
                                actor: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant = assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    pending = db.scalar(select(PendingAgentAction).where(
        PendingAgentAction.id == pending_id, PendingAgentAction.tenant_id == tenant_id).with_for_update())
    if not pending:
        raise HTTPException(status_code=404, detail="Acción pendiente no encontrada")
    if pending.status != PendingActionStatus.PENDING:
        raise HTTPException(status_code=409, detail="La acción ya fue resuelta")
    conversation = db.get(Conversation, pending.conversation_id)
    customer = db.get(Customer, pending.customer_id)
    text = (payload.text or pending.proposed_text).strip()
    integration = db.scalar(select(WhatsappIntegration).where(
        WhatsappIntegration.tenant_id == tenant_id, WhatsappIntegration.active.is_(True)))
    external_id = None
    if integration:
        try:
            external_id = await send_whatsapp_text(integration, customer.phone, text)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Meta rechazó el mensaje") from exc
        if external_id is None and not tenant.is_demo:
            raise HTTPException(status_code=409, detail="El token de WhatsApp no está disponible")
    elif not tenant.is_demo:
        raise HTTPException(status_code=409, detail="WhatsApp no está configurado")
    message = save_message(db, tenant_id=tenant_id, conversation_id=conversation.id,
                           direction=MessageDirection.OUTBOUND, message_type="text",
                           text=text, external_id=external_id)
    pending.status = PendingActionStatus.APPROVED
    pending.resolved_by_id, pending.resolution_note, pending.resolved_at = actor.id, payload.note, utcnow()
    run = db.get(AgentRun, pending.run_id)
    run.status = AgentRunStatus.COMPLETED
    run.suggested_reply = text
    conversation.bot_state = "AGENT_APPROVED"
    conversation.last_message_at = utcnow()
    audit(db, user=actor, tenant_id=tenant_id, action="sales.reply_approved",
          entity_type="pending_agent_action", entity_id=pending.id)
    db.commit()
    return {"ok": True, "message_id": message.id, "status": pending.status.value}


@router.post("/tenants/{tenant_id}/sales/pending/{pending_id}/reject")
def sales_pending_reject(tenant_id: str, pending_id: str, payload: PendingActionResolveInput,
                         actor: User = Depends(current_user), db: Session = Depends(get_db)):
    assert_tenant_access(db, actor, tenant_id, {Role.TENANT_OWNER, Role.TENANT_MANAGER, Role.SELLER})
    pending = db.scalar(select(PendingAgentAction).where(
        PendingAgentAction.id == pending_id, PendingAgentAction.tenant_id == tenant_id).with_for_update())
    if not pending:
        raise HTTPException(status_code=404, detail="Acción pendiente no encontrada")
    if pending.status != PendingActionStatus.PENDING:
        raise HTTPException(status_code=409, detail="La acción ya fue resuelta")
    pending.status = PendingActionStatus.REJECTED
    pending.resolved_by_id, pending.resolution_note, pending.resolved_at = actor.id, payload.note, utcnow()
    conversation = db.get(Conversation, pending.conversation_id)
    conversation.bot_state = "START"
    audit(db, user=actor, tenant_id=tenant_id, action="sales.reply_rejected",
          entity_type="pending_agent_action", entity_id=pending.id)
    db.commit()
    return {"ok": True, "status": pending.status.value}


@router.get("/tracking/{tracking_token}")
def public_tracking(tracking_token: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    delivery = db.scalar(select(Delivery).where(Delivery.tracking_token == tracking_token))
    if not delivery:
        raise HTTPException(status_code=404, detail="Tracking no encontrado")
    expires_at = delivery.tracking_expires_at
    now = utcnow() if expires_at.tzinfo else utcnow().replace(tzinfo=None)
    if expires_at < now:
        raise HTTPException(status_code=410, detail="El enlace de tracking expiró")
    order = db.get(Order, delivery.order_id)
    events = db.scalars(select(DeliveryEvent).where(
        DeliveryEvent.delivery_id == delivery.id
    ).order_by(DeliveryEvent.created_at)).all()
    return {
        "order_id": order.id[-8:],
        "order_status": order.status.value,
        "delivery_status": delivery.status.value,
        "latitude": delivery.current_latitude,
        "longitude": delivery.current_longitude,
        "updated_at": delivery.updated_at,
        "timeline": [{
            "status": event.status.value,
            "created_at": event.created_at,
        } for event in events],
    }
