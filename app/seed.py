"""Crea o actualiza el superadmin inicial sin imprimir credenciales."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .auth import hash_password
from .database import SessionLocal, create_schema
from .models import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    Customer,
    Delivery,
    DeliveryEvent,
    DeliveryStatus,
    Invoice,
    InvoiceStatus,
    MessageDirection,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethodConfig,
    PaymentStatus,
    Product,
    Role,
    SalesObjection,
    SalesPlaybook,
    Tenant,
    TenantStatus,
    User,
)


def seed_demo_history(db, tenant: Tenant, driver: User) -> None:
    """Carga una operación ficticia, variada e idempotente para recorrer el panel."""
    if db.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == tenant.id)):
        return

    now = datetime.now(timezone.utc)
    products = {
        product.sku: product
        for product in db.scalars(select(Product).where(Product.tenant_id == tenant.id)).all()
    }
    scenarios = [
        {
            "phone": "595981111111", "name": "María González", "tax_id": "4567890-2",
            "address": "Av. España 1450, Asunción", "status": OrderStatus.DELIVERED,
            "payment": "BANCARD", "payment_status": PaymentStatus.APPROVED,
            "delivery": DeliveryStatus.DELIVERED, "hours": 30,
            "items": [("PIZZA-FAM", 1), ("GASEOSA-2L", 1)],
            "messages": [
                (MessageDirection.INBOUND, "Hola, quiero una pizza familiar y una gaseosa"),
                (MessageDirection.OUTBOUND, "¡Perfecto! El total con envío es Gs. 113.000. ¿Dónde entregamos?"),
                (MessageDirection.INBOUND, "Av. España 1450. Pago con tarjeta."),
                (MessageDirection.OUTBOUND, "Pago aprobado. Tu pedido salió y podés seguirlo desde el enlace de tracking."),
            ],
        },
        {
            "phone": "595982222222", "name": "Carlos Benítez", "tax_id": None,
            "address": "Tte. Vera 820, Asunción", "status": OrderStatus.IN_TRANSIT,
            "payment": "CASH_ON_DELIVERY", "payment_status": PaymentStatus.PENDING,
            "delivery": DeliveryStatus.IN_TRANSIT, "hours": 2,
            "items": [("PIZZA-MUZZA", 2)],
            "messages": [
                (MessageDirection.INBOUND, "Dos pizzas muzzarella para hoy a las 20:30"),
                (MessageDirection.OUTBOUND, "Pedido confirmado para 20:30. Podés pagar al recibir."),
                (MessageDirection.OUTBOUND, "Tu delivery ya retiró el pedido y va en camino."),
            ],
        },
        {
            "phone": "595983333333", "name": "Lucía Ferreira", "tax_id": "6123456-7",
            "address": "Mcal. López 3100, Fernando de la Mora", "status": OrderStatus.PREPARING,
            "payment": "BANK_TRANSFER", "payment_status": PaymentStatus.APPROVED,
            "delivery": None, "hours": 1,
            "items": [("PIZZA-FAM", 1)],
            "messages": [
                (MessageDirection.INBOUND, "Quiero la familiar. Te paso comprobante de transferencia."),
                (MessageDirection.OUTBOUND, "Transferencia verificada. Cocina ya está preparando tu pedido."),
            ],
        },
        {
            "phone": "595984444444", "name": "Diego Ramírez", "tax_id": None,
            "address": "Palma 455, Asunción", "status": OrderStatus.PENDING_PAYMENT,
            "payment": "BANCARD", "payment_status": PaymentStatus.PENDING,
            "delivery": None, "hours": 0,
            "items": [("PIZZA-MUZZA", 1), ("GASEOSA-2L", 2)],
            "messages": [
                (MessageDirection.INBOUND, "Una muzza y dos gaseosas"),
                (MessageDirection.OUTBOUND, "Tu pedido está reservado. Completá el pago seguro con Bancard."),
            ],
        },
        {
            "phone": "595985555555", "name": "Sofía Acosta", "tax_id": None,
            "address": "San Lorenzo centro", "status": OrderStatus.CANCELLED,
            "payment": "CASH_ON_DELIVERY", "payment_status": PaymentStatus.REJECTED,
            "delivery": None, "hours": 26,
            "items": [("PIZZA-MUZZA", 1)],
            "messages": [
                (MessageDirection.INBOUND, "Necesito cancelar, ya no estaré en casa."),
                (MessageDirection.OUTBOUND, "Pedido cancelado. El stock quedó liberado."),
            ],
        },
    ]

    for index, scenario in enumerate(scenarios, start=1):
        created = now - timedelta(hours=scenario["hours"])
        customer = Customer(
            tenant_id=tenant.id, phone=scenario["phone"], name=scenario["name"],
            tax_id=scenario["tax_id"], address=scenario["address"],
            latitude="-25.2867", longitude="-57.3333", created_at=created,
        )
        db.add(customer)
        db.flush()
        subtotal = sum(products[sku].price * quantity for sku, quantity in scenario["items"])
        shipping = 10000 if scenario["status"] != OrderStatus.CANCELLED else 0
        order = Order(
            tenant_id=tenant.id, customer_id=customer.id, status=scenario["status"],
            subtotal=subtotal, shipping=shipping, total=subtotal + shipping,
            payment_method=scenario["payment"], address=scenario["address"],
            latitude=customer.latitude, longitude=customer.longitude,
            requested_slot="Hoy 20:30" if index == 2 else "Lo antes posible",
            notes=f"Pedido demostrativo #{index}", source="whatsapp", created_at=created, updated_at=created,
        )
        db.add(order)
        db.flush()
        for sku, quantity in scenario["items"]:
            product = products[sku]
            db.add(OrderItem(order_id=order.id, product_id=product.id, product_name=product.name,
                             unit_price=product.price, quantity=quantity, subtotal=product.price * quantity))
        db.add(Payment(
            tenant_id=tenant.id, order_id=order.id, provider=scenario["payment"],
            status=scenario["payment_status"], amount=order.total,
            external_id=f"DEMO-{index:04d}", idempotency_key=f"demo-history-{index}",
            checkout_url="https://comercios.bancard.com.py/checkout/demo" if scenario["payment"] == "BANCARD" else None,
            created_at=created, updated_at=created,
        ))
        invoice_status = InvoiceStatus.CANCELLED if scenario["status"] == OrderStatus.CANCELLED else (
            InvoiceStatus.ISSUED if scenario["payment_status"] == PaymentStatus.APPROVED else InvoiceStatus.PENDING
        )
        db.add(Invoice(
            tenant_id=tenant.id, order_id=order.id, status=invoice_status,
            customer_name=customer.name, tax_id=customer.tax_id or "", amount=order.total,
            document_number=f"001-001-{index:07d}" if invoice_status == InvoiceStatus.ISSUED else "",
            external_id=f"SIFEN-DEMO-{index}" if invoice_status == InvoiceStatus.ISSUED else "",
            issued_at=created + timedelta(minutes=5) if invoice_status == InvoiceStatus.ISSUED else None,
            created_at=created, updated_at=created,
        ))
        conversation = Conversation(
            tenant_id=tenant.id, customer_id=customer.id,
            status=ConversationStatus.CLOSED if scenario["status"] in {OrderStatus.DELIVERED, OrderStatus.CANCELLED} else ConversationStatus.BOT,
            active_order_id=order.id, bot_state="ORDER_CONFIRMED", unread_count=1 if index in {3, 4} else 0,
            last_message_at=created + timedelta(minutes=len(scenario["messages"]) * 2), created_at=created,
        )
        db.add(conversation)
        db.flush()
        for offset, (direction, text) in enumerate(scenario["messages"]):
            db.add(ConversationMessage(
                tenant_id=tenant.id, conversation_id=conversation.id, direction=direction,
                message_type="text", text=text, created_at=created + timedelta(minutes=offset * 2),
            ))
        if scenario["delivery"]:
            delivery = Delivery(
                tenant_id=tenant.id, order_id=order.id, driver_id=driver.id,
                status=scenario["delivery"], current_latitude="-25.2920", current_longitude="-57.5810",
                proof_note="Entregado a María" if scenario["delivery"] == DeliveryStatus.DELIVERED else None,
                created_at=created + timedelta(minutes=20), updated_at=now,
            )
            db.add(delivery)
            db.flush()
            event_statuses = [DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP]
            if scenario["delivery"] == DeliveryStatus.IN_TRANSIT:
                event_statuses.append(DeliveryStatus.IN_TRANSIT)
            else:
                event_statuses.extend([DeliveryStatus.IN_TRANSIT, DeliveryStatus.ARRIVED, DeliveryStatus.DELIVERED])
            for offset, status in enumerate(event_statuses):
                db.add(DeliveryEvent(
                    tenant_id=tenant.id, delivery_id=delivery.id, status=status,
                    latitude="-25.2920", longitude="-57.5810", note=f"Demo: {status.value}",
                    created_at=created + timedelta(minutes=20 + offset * 12),
                ))


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
        driver = User(tenant_id=tenant.id, email="delivery@ventasbot.local", name="Delivery Demo",
                      password_hash=hash_password(demo_password), role=Role.DRIVER)
        db.add(driver)
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
    db.flush()
    playbook = db.scalar(select(SalesPlaybook).where(SalesPlaybook.tenant_id == tenant.id))
    if not playbook:
        db.add(SalesPlaybook(tenant_id=tenant.id))
    demo_objections = (
        ("PRECIO", "es caro,muy caro,no me alcanza", "Entiendo. Podemos revisar una opción más económica sin perder calidad. ¿Qué presupuesto tenés?"),
        ("ENVÍO", "tarda mucho,demora", "Te confirmo el horario antes de cerrar la compra y podrás seguir el delivery en tiempo real."),
        ("CONFIANZA", "es seguro,no confio,no confío", "Tu pedido queda registrado y el pago con tarjeta se realiza en el checkout seguro de Bancard."),
    )
    existing_objections = set(db.scalars(select(SalesObjection.name).where(
        SalesObjection.tenant_id == tenant.id)).all())
    for name, triggers, response in demo_objections:
        if name not in existing_objections:
            db.add(SalesObjection(tenant_id=tenant.id, name=name, triggers=triggers, response=response))
    seed_demo_history(db, tenant, driver)


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
