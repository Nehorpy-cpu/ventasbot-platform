"""Lo que el panel necesita para operar un pedido de punta a punta."""

from __future__ import annotations

from tests.test_mvp_platform import auth, create_tenant, platform  # noqa: F401


def montar(client):
    root = auth(client, "root@ventasbot.com", "super-segura-123")
    tenant = create_tenant(client, root, slug="operativa", email="owner@operativa.com")
    owner = auth(client, "owner@operativa.com", "owner-segura-123", "operativa")
    producto = client.post(f"/api/tenants/{tenant['id']}/products", headers=owner, json={
        "sku": "P1", "name": "Producto", "price": 50000, "stock": 10}).json()
    pedido = client.post(f"/api/tenants/{tenant['id']}/orders", headers=owner, json={
        "customer": {"phone": "595981000111", "name": "Ana"},
        "items": [{"product_id": producto["id"], "quantity": 2}]}).json()
    return tenant, owner, pedido


def test_el_pedido_dice_a_que_estados_puede_moverse(platform):
    """Sin esto el panel tendría que duplicar la máquina de estados en JS."""
    client, _ = platform
    tenant, owner, pedido = montar(client)
    assert pedido["proximos_estados"] == ["PENDING_PAYMENT", "CONFIRMED", "CANCELLED"]

    client.post(f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/status",
                headers=owner, json={"status": "CONFIRMED"})
    detalle = client.get(f"/api/tenants/{tenant['id']}/orders/{pedido['id']}", headers=owner).json()
    assert detalle["pedido"]["proximos_estados"] == ["PREPARING", "CANCELLED"]


def test_un_pedido_entregado_no_ofrece_mas_pasos(platform):
    client, _ = platform
    tenant, owner, pedido = montar(client)
    for estado in ("CONFIRMED", "PREPARING", "READY", "ASSIGNED", "IN_TRANSIT", "DELIVERED"):
        r = client.post(f"/api/tenants/{tenant['id']}/orders/{pedido['id']}/status",
                        headers=owner, json={"status": estado})
        assert r.status_code == 200, r.text
    assert r.json()["proximos_estados"] == []


def test_el_detalle_trae_pedido_pagos_saldo_y_entrega(platform):
    client, _ = platform
    tenant, owner, pedido = montar(client)
    ruta = f"/api/tenants/{tenant['id']}/orders/{pedido['id']}"

    detalle = client.get(ruta, headers=owner).json()
    assert detalle["saldo"] == 100000
    assert detalle["pagos"] == []
    assert detalle["entrega"] is None

    client.post(f"{ruta}/payments", headers=owner, json={
        "provider": "CASH", "status": "APPROVED", "amount": 40000, "idempotency_key": "panel-abc-1"})
    detalle = client.get(ruta, headers=owner).json()
    assert detalle["saldo"] == 60000
    assert len(detalle["pagos"]) == 1


def test_la_lista_de_repartidores_sale_filtrada_por_rol(platform):
    client, _ = platform
    tenant, owner, _pedido = montar(client)
    for correo, rol in (("chofer@operativa.com", "DRIVER"), ("vende@operativa.com", "SELLER")):
        client.post(f"/api/tenants/{tenant['id']}/users", headers=owner, json={
            "email": correo, "name": correo.split("@")[0], "password": "clave-segura-1", "role": rol})

    todos = client.get(f"/api/tenants/{tenant['id']}/users", headers=owner).json()
    choferes = client.get(f"/api/tenants/{tenant['id']}/users?rol=DRIVER", headers=owner).json()
    assert len(todos) == 3                      # owner + chofer + vendedor
    assert [u["email"] for u in choferes] == ["chofer@operativa.com"]


def test_una_empresa_no_ve_el_equipo_de_otra(platform):
    client, _ = platform
    tenant, owner, _pedido = montar(client)
    root = auth(client, "root@ventasbot.com", "super-segura-123")
    otra = create_tenant(client, root, slug="ajena", email="owner@ajena.com")
    assert client.get(f"/api/tenants/{otra['id']}/users", headers=owner).status_code == 403


def test_el_detalle_de_un_pedido_ajeno_da_404(platform):
    client, _ = platform
    tenant, owner, _pedido = montar(client)
    r = client.get(f"/api/tenants/{tenant['id']}/orders/ord_inexistente", headers=owner)
    assert r.status_code == 404


def test_la_pagina_de_seguimiento_se_sirve_publica(platform):
    """Sin token de sesión: es el link que se le manda al cliente."""
    client, _ = platform
    r = client.get("/seguimiento/cualquier-token")
    assert r.status_code == 200
    assert "Seguimiento" in r.text
    assert "/api/tracking/" in r.text
