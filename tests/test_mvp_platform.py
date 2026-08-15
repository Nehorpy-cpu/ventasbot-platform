"""Pruebas E2E del núcleo SaaS usando SQLite en memoria."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import Role, User
from app.crm import bot_reply, get_or_create_conversation
from app.mensajes import MensajeEntrante
from app.models import Customer, Order, PaymentMethodConfig, Product, Tenant


@pytest.fixture
def platform():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with TestingSession() as db:
        db.add(User(
            tenant_id=None,
            email="root@ventasbot.test",
            name="Root",
            password_hash=hash_password("super-segura-123"),
            role=Role.PLATFORM_ADMIN,
        ))
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, TestingSession
    app.dependency_overrides.clear()


def auth(client: TestClient, email: str, password: str, tenant_slug: str | None = None) -> dict[str, str]:
    response = client.post("/api/auth/login", json={
        "email": email, "password": password, "tenant_slug": tenant_slug
    })
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_tenant(client: TestClient, root_headers: dict[str, str], *, slug: str, email: str):
    response = client.post("/api/platform/tenants", headers=root_headers, json={
        "name": slug.replace("-", " ").title(),
        "slug": slug,
        "owner_name": "Dueño Demo",
        "owner_email": email,
        "owner_password": "owner-segura-123",
        "is_demo": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_superadmin_crea_tenant_y_owner_inicia_sesion(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="tienda-demo", email="owner@demo.test")
    assert tenant["status"] == "ACTIVE"
    assert tenant["is_demo"] is True

    owner = auth(client, "owner@demo.test", "owner-segura-123", "tienda-demo")
    me = client.get("/api/me", headers=owner)
    assert me.status_code == 200
    assert me.json()["tenant_id"] == tenant["id"]
    assert me.json()["role"] == "TENANT_OWNER"


def test_panel_web_se_sirve(platform):
    client, _ = platform
    response = client.get("/panel/")
    assert response.status_code == 200
    assert "VentasBot" in response.text
    assert "/panel/app.js" in response.text


def test_importacion_catalogo_es_idempotente_y_desactiva_faltantes(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="catalog-sync", email="owner@catalog.test")
    owner = auth(client, "owner@catalog.test", "owner-segura-123", "catalog-sync")
    url = f"/api/tenants/{tenant['id']}/catalog/import"
    first = client.post(url, headers=owner, json={"source": "META", "products": [
        {"sku": "A", "name": "A", "price": 1000, "stock": 3},
        {"sku": "B", "name": "B", "price": 2000, "stock": 4}
    ]})
    assert first.status_code == 200 and first.json()["created"] == 2
    second = client.post(url, headers=owner, json={"source": "WEB", "deactivate_missing": True,
        "products": [{"sku": "A", "name": "A nuevo", "price": 1500, "stock": 5}]})
    assert second.status_code == 200
    assert second.json() == {"source": "WEB", "created": 0, "updated": 1,
                             "deactivated": 1, "total_received": 1}
    rows = client.get(f"/api/tenants/{tenant['id']}/products", headers=owner).json()
    assert {row["sku"]: row["active"] for row in rows} == {"A": True, "B": False}


def test_login_empresarial_exige_slug(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    create_tenant(client, root, slug="empresa-login", email="owner@login.test")
    without_slug = client.post("/api/auth/login", json={
        "email": "owner@login.test", "password": "owner-segura-123"
    })
    with_slug = client.post("/api/auth/login", json={
        "email": "owner@login.test", "password": "owner-segura-123", "tenant_slug": "empresa-login"
    })
    assert without_slug.status_code == 401
    assert with_slug.status_code == 200


def test_aislamiento_estricto_entre_empresas(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    first = create_tenant(client, root, slug="empresa-uno", email="uno@test.local")
    second = create_tenant(client, root, slug="empresa-dos", email="dos@test.local")
    first_owner = auth(client, "uno@test.local", "owner-segura-123", "empresa-uno")

    own = client.get(f"/api/tenants/{first['id']}/products", headers=first_owner)
    foreign = client.get(f"/api/tenants/{second['id']}/products", headers=first_owner)
    assert own.status_code == 200
    assert foreign.status_code == 403


def test_venta_pago_stock_delivery_y_tracking(platform):
    client, SessionLocal = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="pizzeria-central", email="owner@pizza.test")
    owner = auth(client, "owner@pizza.test", "owner-segura-123", "pizzeria-central")
    tenant_id = tenant["id"]

    product_response = client.post(f"/api/tenants/{tenant_id}/products", headers=owner, json={
        "sku": "PIZZA-FAM",
        "name": "Pizza familiar",
        "description": "Muzzarella",
        "price": 85000,
        "stock": 10,
    })
    assert product_response.status_code == 201, product_response.text
    product = product_response.json()

    driver_response = client.post(f"/api/tenants/{tenant_id}/users", headers=owner, json={
        "email": "delivery@pizza.test",
        "name": "Juan Delivery",
        "password": "delivery-segura-123",
        "role": "DRIVER",
    })
    assert driver_response.status_code == 201, driver_response.text
    driver = driver_response.json()

    order_response = client.post(f"/api/tenants/{tenant_id}/orders", headers=owner, json={
        "customer": {
            "phone": "595981123456",
            "name": "Ana",
            "address": "Centro, Asunción",
            "latitude": "-25.2867",
            "longitude": "-57.3333"
        },
        "items": [{"product_id": product["id"], "quantity": 2}],
        "shipping": 15000,
        "payment_method": "BANCARD",
        "requested_slot": "20:00-21:00",
        "source": "whatsapp"
    })
    assert order_response.status_code == 201, order_response.text
    order = order_response.json()
    assert order["subtotal"] == 170000
    assert order["total"] == 185000
    assert order["status"] == "PENDING_CONFIRMATION"

    payment_response = client.post(
        f"/api/tenants/{tenant_id}/orders/{order['id']}/payments",
        headers=owner,
            json={
                "provider": "BANCARD",
                "status": "PENDING",
                "amount": 185000,
                "external_id": "bancard-process-1",
            "idempotency_key": "pay-order-demo-0001"
        },
    )
    assert payment_response.status_code == 201, payment_response.text
    callback = client.post(f"/webhooks/payments/bancard/{tenant_id}", json={"payment": {
        "link_alias": "bancard-process-1", "status": "confirmed",
        "response_code": "00", "amount": 185000,
    }})
    assert callback.status_code == 200, callback.text

    orders = client.get(f"/api/tenants/{tenant_id}/orders", headers=owner).json()
    assert orders[0]["status"] == "CONFIRMED"
    products = client.get(f"/api/tenants/{tenant_id}/products", headers=owner).json()
    assert products[0]["stock"] == 8

    for status in ("PREPARING", "READY"):
        response = client.post(
            f"/api/tenants/{tenant_id}/orders/{order['id']}/status",
            headers=owner,
            json={"status": status},
        )
        assert response.status_code == 200, response.text

    assign = client.post(
        f"/api/tenants/{tenant_id}/orders/{order['id']}/delivery/assign",
        headers=owner,
        json={"driver_id": driver["id"]},
    )
    assert assign.status_code == 200, assign.text
    delivery = assign.json()

    driver_headers = auth(client, "delivery@pizza.test", "delivery-segura-123", "pizzeria-central")
    for status in ("PICKED_UP", "IN_TRANSIT", "ARRIVED", "DELIVERED"):
        update = client.post(
            f"/api/tenants/{tenant_id}/deliveries/{delivery['id']}/status",
            headers=driver_headers,
            json={
                "status": status,
                "latitude": "-25.29",
                "longitude": "-57.34",
                "proof_note": "PIN validado" if status == "DELIVERED" else None,
            },
        )
        assert update.status_code == 200, update.text

    tracking = client.get(f"/api/tracking/{delivery['tracking_token']}")
    assert tracking.status_code == 200
    assert tracking.json()["delivery_status"] == "DELIVERED"
    assert tracking.json()["order_status"] == "DELIVERED"

    with SessionLocal() as db:
        from app.models import AuditLog
        assert db.query(AuditLog).count() >= 9


def test_pago_idempotente_no_duplica(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="tienda-idempotente", email="owner@idem.test")
    owner = auth(client, "owner@idem.test", "owner-segura-123", "tienda-idempotente")
    tenant_id = tenant["id"]
    product = client.post(f"/api/tenants/{tenant_id}/products", headers=owner, json={
        "sku": "SKU-1", "name": "Producto", "price": 10000, "stock": 5
    }).json()
    order = client.post(f"/api/tenants/{tenant_id}/orders", headers=owner, json={
        "customer": {"phone": "595991111111"},
        "items": [{"product_id": product["id"], "quantity": 1}],
    }).json()
    payload = {
        "provider": "CASH", "status": "APPROVED", "amount": 10000,
        "idempotency_key": "same-payment-key"
    }
    first = client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/payments", headers=owner, json=payload)
    second = client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/payments", headers=owner, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert client.get(f"/api/tenants/{tenant_id}/products", headers=owner).json()[0]["stock"] == 4


def test_bot_captura_carrito_ubicacion_horario_y_pago_al_recibir(platform):
    _, SessionLocal = platform
    with SessionLocal() as db:
        tenant = Tenant(name="Bot Demo", slug="bot-demo")
        db.add(tenant)
        db.flush()
        product = Product(tenant_id=tenant.id, sku="PIZZA-FAM", name="Pizza familiar", price=85000, stock=4)
        customer = Customer(tenant_id=tenant.id, phone="595981000000", name="Ana")
        db.add_all([product, customer])
        db.flush()
        conversation = get_or_create_conversation(db, tenant.id, customer.id)
        db.add(PaymentMethodConfig(tenant_id=tenant.id, code="CASH_ON_DELIVERY",
                                   display_name="Pago al recibir", enabled=True))
        db.flush()

        def incoming(kind, text="", data=None):
            return MensajeEntrante(id=f"msg-{kind}-{text}", de=customer.phone, tipo=kind,
                                   texto=text, timestamp="1", nombre=customer.name, datos=data or {},
                                   phone_number_id="meta-demo")

        reply = bot_reply(db, conversation, incoming("order", data={"product_items": [
            {"product_retailer_id": "PIZZA-FAM", "quantity": "2"}
        ]}))
        assert "170.000" in reply and conversation.bot_state == "WAITING_LOCATION"
        assert "Ubicación" in bot_reply(db, conversation, incoming("text", "mi casa"))
        bot_reply(db, conversation, incoming("location", data={
            "latitude": -25.30, "longitude": -57.60, "name": "Casa"
        }))
        assert conversation.bot_state == "WAITING_SLOT"
        bot_reply(db, conversation, incoming("text", "hoy 19 a 20"))
        assert conversation.bot_state == "WAITING_INVOICE"
        bot_reply(db, conversation, incoming("text", "consumidor final"))
        assert conversation.bot_state == "WAITING_PAYMENT"
        final = bot_reply(db, conversation, incoming("text", "pago al recibir"))
        order = db.get(Order, conversation.active_order_id)
        assert "capturado" in final
        assert order.status.value == "CONFIRMED"
        assert order.requested_slot == "hoy 19 a 20"
        assert product.stock == 2


def test_delivery_rechaza_salto_de_estado(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="delivery-state", email="owner@state.test")
    owner = auth(client, "owner@state.test", "owner-segura-123", "delivery-state")
    tenant_id = tenant["id"]
    product = client.post(f"/api/tenants/{tenant_id}/products", headers=owner,
                          json={"sku": "ONE", "name": "Uno", "price": 1000, "stock": 2}).json()
    driver = client.post(f"/api/tenants/{tenant_id}/users", headers=owner, json={
        "email": "driver@state.test", "name": "Driver", "password": "driver-segura-123", "role": "DRIVER"
    }).json()
    order = client.post(f"/api/tenants/{tenant_id}/orders", headers=owner, json={
        "customer": {"phone": "595981999999"}, "items": [{"product_id": product["id"], "quantity": 1}]
    }).json()
    client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/payments", headers=owner, json={
        "provider": "CASH", "status": "APPROVED", "amount": 1000, "idempotency_key": "state-pay"
    })
    for status in ("PREPARING", "READY"):
        client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/status", headers=owner, json={"status": status})
    delivery = client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/delivery/assign",
                           headers=owner, json={"driver_id": driver["id"]}).json()
    invalid = client.post(f"/api/tenants/{tenant_id}/deliveries/{delivery['id']}/status",
                          headers=owner, json={"status": "DELIVERED"})
    assert invalid.status_code == 409


def test_callback_bancard_confirma_pago_autoritativo(platform):
    client, _ = platform
    root = auth(client, "root@ventasbot.test", "super-segura-123")
    tenant = create_tenant(client, root, slug="bancard-callback", email="owner@bancard.test")
    owner = auth(client, "owner@bancard.test", "owner-segura-123", "bancard-callback")
    tenant_id = tenant["id"]
    product = client.post(f"/api/tenants/{tenant_id}/products", headers=owner,
                          json={"sku": "PAY", "name": "Pago", "price": 25000, "stock": 2}).json()
    order = client.post(f"/api/tenants/{tenant_id}/orders", headers=owner, json={
        "customer": {"phone": "595981222222"}, "items": [{"product_id": product["id"], "quantity": 1}]
    }).json()
    pending = client.post(f"/api/tenants/{tenant_id}/orders/{order['id']}/payments", headers=owner, json={
        "provider": "BANCARD", "status": "PENDING", "amount": 25000,
        "external_id": "LINK123", "idempotency_key": "callback-payment-key"
    })
    assert pending.status_code == 201
    callback = client.post(f"/webhooks/payments/bancard/{tenant_id}", json={"payment": {
        "link_alias": "LINK123", "status": "confirmed", "response_code": "00", "amount": 25000
    }})
    assert callback.status_code == 200
    assert client.get(f"/api/tenants/{tenant_id}/orders", headers=owner).json()[0]["status"] == "CONFIRMED"
    assert client.get(f"/api/tenants/{tenant_id}/products", headers=owner).json()[0]["stock"] == 1
    invoices = client.get(f"/api/tenants/{tenant_id}/invoices", headers=owner)
    assert invoices.status_code == 200
    assert invoices.json()[0]["amount"] == 25000
    assert invoices.json()[0]["status"] == "PENDING"
