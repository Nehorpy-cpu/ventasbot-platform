"""Regresiones de la auditoría: bugs reproducidos antes de arreglarlos.

Cada test de acá falló contra el código anterior. Si alguno vuelve a fallar,
volvió un bug conocido, no es un test frágil.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_mvp_platform import auth, create_tenant, platform  # noqa: F401


def montar(client: TestClient, slug: str, correo: str):
    root = auth(client, "root@ventasbot.com", "super-segura-123")
    tenant = create_tenant(client, root, slug=slug, email=correo)
    owner = auth(client, correo, "owner-segura-123", slug)
    producto = client.post(f"/api/tenants/{tenant['id']}/products", headers=owner, json={
        "sku": "SKU-1", "name": "Producto", "price": 1000, "stock": 10,
    }).json()
    return tenant, owner, producto


def crear_pedido(client, tenant, owner, producto, cantidad, telefono):
    return client.post(f"/api/tenants/{tenant['id']}/orders", headers=owner, json={
        "customer": {"phone": telefono},
        "items": [{"product_id": producto["id"], "quantity": cantidad}],
    }).json()


def stock_actual(client, tenant, owner) -> int:
    return client.get(f"/api/tenants/{tenant['id']}/products", headers=owner).json()[0]["stock"]


def test_cancelar_pedido_confirmado_repone_stock(platform):
    """Antes: confirmar descontaba stock y cancelar no lo devolvía nunca."""
    client, _ = platform
    tenant, owner, producto = montar(client, "tienda-stock", "stock@demo.com")
    pedido = crear_pedido(client, tenant, owner, producto, 4, "595981000001")

    ruta = f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/status"
    assert client.post(ruta, headers=owner, json={"status": "CONFIRMED"}).status_code == 200
    assert stock_actual(client, tenant, owner) == 6

    assert client.post(ruta, headers=owner, json={"status": "CANCELLED"}).status_code == 200
    assert stock_actual(client, tenant, owner) == 10


def test_cancelar_pedido_sin_confirmar_no_infla_stock(platform):
    """El contrapeso: si el stock nunca se descontó, cancelar no debe sumarlo."""
    client, _ = platform
    tenant, owner, producto = montar(client, "tienda-stock2", "stock2@demo.com")
    pedido = crear_pedido(client, tenant, owner, producto, 3, "595981000002")

    ruta = f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/status"
    assert client.post(ruta, headers=owner, json={"status": "CANCELLED"}).status_code == 200
    assert stock_actual(client, tenant, owner) == 10


def test_pagos_parciales_no_superan_el_total(platform):
    """Antes: dos pagos de 800 entraban en un pedido de 1000 (se cobraba 1600)."""
    client, _ = platform
    tenant, owner, producto = montar(client, "tienda-pagos", "pagos@demo.com")
    pedido = crear_pedido(client, tenant, owner, producto, 1, "595981000003")
    assert pedido["total"] == 1000
    ruta = f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/payments"

    primero = client.post(ruta, headers=owner, json={
        "provider": "efectivo", "status": "APPROVED", "amount": 800, "idempotency_key": "pago-uno-01"})
    assert primero.status_code == 201, primero.text

    segundo = client.post(ruta, headers=owner, json={
        "provider": "efectivo", "status": "APPROVED", "amount": 800, "idempotency_key": "pago-dos-02"})
    assert segundo.status_code == 400
    assert "saldo" in segundo.json()["detail"]


def test_dos_pagos_parciales_exactos_confirman_el_pedido(platform):
    """600 + 400 sobre un pedido de 1000 sí lo confirma."""
    client, _ = platform
    tenant, owner, producto = montar(client, "tienda-pagos2", "pagos2@demo.com")
    pedido = crear_pedido(client, tenant, owner, producto, 1, "595981000004")
    ruta = f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/payments"

    client.post(ruta, headers=owner, json={
        "provider": "efectivo", "status": "APPROVED", "amount": 600, "idempotency_key": "parcial-01"})
    client.post(ruta, headers=owner, json={
        "provider": "efectivo", "status": "APPROVED", "amount": 400, "idempotency_key": "parcial-02"})

    pedidos = client.get(f"/api/tenants/{tenant['id']}/orders", headers=owner).json()
    assert pedidos[0]["status"] == "CONFIRMED"
    assert stock_actual(client, tenant, owner) == 9


def test_actualizar_delivery_sin_coordenadas_no_borra_la_ubicacion(platform):
    """Antes: mandar solo el estado ponía lat/long en NULL y se perdía el rastro."""
    client, _ = platform
    tenant, owner, producto = montar(client, "tienda-envios", "envios@demo.com")
    chofer = client.post(f"/api/tenants/{tenant['id']}/users", headers=owner, json={
        "email": "chofer@demo.com", "name": "Chofer", "password": "chofer-segura-1", "role": "DRIVER",
    }).json()
    pedido = crear_pedido(client, tenant, owner, producto, 1, "595981000005")
    for estado in ("CONFIRMED", "PREPARING", "READY"):
        client.post(f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/status",
                    headers=owner, json={"status": estado})
    entrega = client.post(f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/delivery/assign",
                          headers=owner, json={"driver_id": chofer["id"]}).json()

    ruta = f"/api/tenants/{tenant['id']}/deliveries/{entrega['id']}/status"
    client.post(ruta, headers=owner, json={
        "status": "IN_TRANSIT", "latitude": "-25.28", "longitude": "-57.63"})
    llegada = client.post(ruta, headers=owner, json={"status": "ARRIVED"}).json()

    assert llegada["current_latitude"] == "-25.28"
    assert llegada["current_longitude"] == "-57.63"


def test_login_bloquea_tras_demasiados_intentos(platform):
    """Sin freno, el panel quedaba abierto a fuerza bruta de contraseñas."""
    from app.api import _intentos

    client, _ = platform
    _intentos.clear()
    codigos = [
        client.post("/api/auth/login", json={
            "email": "root@ventasbot.com", "password": f"mala-{i}"}).status_code
        for i in range(12)
    ]
    assert 429 in codigos, "debería frenar antes del intento 12"
    _intentos.clear()


def test_token_de_rol_viejo_queda_invalido(platform):
    """Un token emitido antes de desactivar/cambiar el rol no debe seguir sirviendo."""
    from app.models import Role, User

    client, Sesion = platform
    tenant, owner, _ = montar(client, "tienda-roles", "roles@demo.com")
    assert client.get("/api/me", headers=owner).status_code == 200

    with Sesion() as db:
        usuario = db.query(User).filter(User.email == "roles@demo.com").one()
        usuario.role = Role.SELLER
        db.commit()

    assert client.get("/api/me", headers=owner).status_code == 401
