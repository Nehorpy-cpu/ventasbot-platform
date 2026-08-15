"""Crea o actualiza el superadmin inicial sin imprimir credenciales."""

from __future__ import annotations

import os

from sqlalchemy import func, select

from .auth import hash_password
from .database import SessionLocal, create_schema
from .models import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    Customer,
    MessageDirection,
    PaymentMethodConfig,
    Product,
    Role,
    Tenant,
    TenantStatus,
    User,
)


def seed_demo(db, demo_password: str) -> None:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "pizzeria-demo"))
    if not tenant:
        tenant = Tenant(name="Pizzería Demo", slug="pizzeria-demo", status=TenantStatus.ACTIVE, is_demo=True)
        db.add(tenant)
        db.flush()
    owner = db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == "demo@ventasbot.local"))
    if not owner:
        owner = User(tenant_id=tenant.id, email="demo@ventasbot.local", name="Dueño Demo",
                     password_hash=hash_password(demo_password), role=Role.TENANT_OWNER)
        db.add(owner)
    else:
        owner.password_hash = hash_password(demo_password)
    driver = db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == "delivery@ventasbot.local"))
    if not driver:
        db.add(User(tenant_id=tenant.id, email="delivery@ventasbot.local", name="Delivery Demo",
                    password_hash=hash_password(demo_password), role=Role.DRIVER))
    if not db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant.id)):
        db.add_all([
            Product(tenant_id=tenant.id, sku="PIZZA-MUZZA", name="Pizza muzzarella", price=65000, stock=30),
            Product(tenant_id=tenant.id, sku="PIZZA-FAM", name="Pizza familiar", price=85000, stock=20),
            Product(tenant_id=tenant.id, sku="GASEOSA-2L", name="Gaseosa 2 litros", price=18000, stock=50),
        ])
    for code, name, instructions in (
        ("CASH_ON_DELIVERY", "Pago al recibir", "Pagá en efectivo o POS cuando llegue tu pedido."),
        ("BANK_TRANSFER", "Transferencia bancaria", "La empresa enviará la cuenta y verificará el comprobante."),
    ):
        config = db.scalar(select(PaymentMethodConfig).where(
            PaymentMethodConfig.tenant_id == tenant.id, PaymentMethodConfig.code == code))
        if not config:
            db.add(PaymentMethodConfig(tenant_id=tenant.id, code=code, display_name=name,
                                       enabled=True, instructions=instructions))
    customer = db.scalar(select(Customer).where(
        Customer.tenant_id == tenant.id, Customer.phone == "595981123456"))
    if not customer:
        customer = Customer(tenant_id=tenant.id, phone="595981123456", name="Ana Demo",
                            address="Centro, Asunción")
        db.add(customer)
        db.flush()
    conversation = db.scalar(select(Conversation).where(
        Conversation.tenant_id == tenant.id, Conversation.customer_id == customer.id))
    if not conversation:
        conversation = Conversation(tenant_id=tenant.id, customer_id=customer.id,
                                    status=ConversationStatus.HUMAN, unread_count=1)
        db.add(conversation)
        db.flush()
        db.add_all([
            ConversationMessage(tenant_id=tenant.id, conversation_id=conversation.id,
                                direction=MessageDirection.INBOUND, message_type="text",
                                text="Hola, quiero ver el catálogo"),
            ConversationMessage(tenant_id=tenant.id, conversation_id=conversation.id,
                                direction=MessageDirection.OUTBOUND, message_type="text",
                                text="¡Hola Ana! Te muestro lo disponible."),
        ])


def main() -> None:
    email = os.getenv("SUPERADMIN_EMAIL", "admin@ventasbot.local").strip().lower()
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
        if os.getenv("SEED_DEMO", "0") == "1":
            demo_password = os.getenv("DEMO_PASSWORD", "")
            if len(demo_password) < 8 or demo_password == "cambiar-clave-demo":
                raise SystemExit("Definí DEMO_PASSWORD con al menos 8 caracteres")
            seed_demo(db, demo_password)
        db.commit()
    print(f"Superadmin listo: {email}")


if __name__ == "__main__":
    main()
