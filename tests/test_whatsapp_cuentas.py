"""Cada empresa carga su propio número de WhatsApp desde el panel."""

from __future__ import annotations

from sqlalchemy import select

from app.cripto import descifrar
from app.models import WhatsAppAccount
from tests.test_mvp_platform import auth, create_tenant, platform  # noqa: F401

NUMERO_A = "111111111111111"
NUMERO_B = "222222222222222"


def empresa(client, slug: str, correo: str):
    root = auth(client, "root@ventasbot.com", "super-segura-123")
    tenant = create_tenant(client, root, slug=slug, email=correo)
    owner = auth(client, correo, "owner-segura-123", slug)
    return tenant, owner


def test_la_empresa_carga_su_numero_y_el_token_no_vuelve(platform):
    client, Sesion = platform
    tenant, owner = empresa(client, "pizzeria-uno", "owner@pizzauno.com")

    r = client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner, json={
        "phone_number_id": NUMERO_A,
        "access_token": "EAAG-token-larguisimo-de-la-pizzeria",
        "display_phone_number": "595981111111",
        "waba_id": "WABA-1",
    })
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["phone_number_id"] == NUMERO_A
    assert cuerpo["token_cargado"] is True
    assert "EAAG-token-larguisimo-de-la-pizzeria" not in r.text
    assert cuerpo["token_enmascarado"].endswith("eria")
    assert cuerpo["verificado_en"] is None

    with Sesion() as db:
        cuenta = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant["id"]))
        assert cuenta.access_token_cifrado != "EAAG-token-larguisimo-de-la-pizzeria"
        assert descifrar(cuenta.access_token_cifrado) == "EAAG-token-larguisimo-de-la-pizzeria"


def test_dos_empresas_no_pueden_reclamar_el_mismo_numero(platform):
    client, _ = platform
    primera, owner1 = empresa(client, "empresa-a", "a@empresas.com")
    segunda, owner2 = empresa(client, "empresa-b", "b@empresas.com")

    assert client.put(f"/api/tenants/{primera['id']}/whatsapp", headers=owner1, json={
        "phone_number_id": NUMERO_A, "access_token": "token-de-a"}).status_code == 200

    choque = client.put(f"/api/tenants/{segunda['id']}/whatsapp", headers=owner2, json={
        "phone_number_id": NUMERO_A, "access_token": "token-de-b"})
    assert choque.status_code == 409
    assert "otra empresa" in choque.json()["detail"]


def test_editar_sin_mandar_token_conserva_el_guardado(platform):
    client, Sesion = platform
    tenant, owner = empresa(client, "empresa-edita", "edita@empresas.com")
    client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner, json={
        "phone_number_id": NUMERO_A, "access_token": "token-original"})

    r = client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner, json={
        "phone_number_id": NUMERO_B, "display_phone_number": "595982222222"})
    assert r.status_code == 200
    assert r.json()["phone_number_id"] == NUMERO_B

    with Sesion() as db:
        cuenta = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant["id"]))
        assert descifrar(cuenta.access_token_cifrado) == "token-original"


def test_alta_sin_token_es_rechazada(platform):
    client, _ = platform
    tenant, owner = empresa(client, "empresa-sin-token", "sintoken@empresas.com")
    r = client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner, json={
        "phone_number_id": NUMERO_A})
    assert r.status_code == 400
    assert "access_token" in r.json()["detail"]


def test_un_vendedor_no_puede_tocar_las_credenciales(platform):
    client, _ = platform
    tenant, owner = empresa(client, "empresa-roles", "roles@empresas.com")
    client.post(f"/api/tenants/{tenant['id']}/users", headers=owner, json={
        "email": "vendedor@empresas.com", "name": "Vendedor",
        "password": "vendedor-segura-1", "role": "SELLER"})
    vendedor = auth(client, "vendedor@empresas.com", "vendedor-segura-1", "empresa-roles")

    r = client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=vendedor, json={
        "phone_number_id": NUMERO_A, "access_token": "token-robado"})
    assert r.status_code == 403


def test_una_empresa_no_ve_las_credenciales_de_otra(platform):
    client, _ = platform
    primera, owner1 = empresa(client, "espia-a", "espia-a@empresas.com")
    segunda, _owner2 = empresa(client, "espia-b", "espia-b@empresas.com")
    client.put(f"/api/tenants/{primera['id']}/whatsapp", headers=owner1, json={
        "phone_number_id": NUMERO_A, "access_token": "token-privado"})

    assert client.get(f"/api/tenants/{segunda['id']}/whatsapp", headers=owner1).status_code == 403


def test_sin_numero_cargado_el_panel_recibe_404(platform):
    client, _ = platform
    tenant, owner = empresa(client, "empresa-vacia", "vacia@empresas.com")
    r = client.get(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner)
    assert r.status_code == 404


def test_el_numero_debe_ser_numerico(platform):
    client, _ = platform
    tenant, owner = empresa(client, "empresa-formato", "formato@empresas.com")
    r = client.put(f"/api/tenants/{tenant['id']}/whatsapp", headers=owner, json={
        "phone_number_id": "no-es-un-id", "access_token": "token"})
    assert r.status_code == 422
