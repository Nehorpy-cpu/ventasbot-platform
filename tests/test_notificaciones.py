"""Avisos por WhatsApp al cliente cuando su pedido cambia de estado."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import notificaciones
from app.cripto import cifrar
from app.database import Base
from app.models import (
    Customer,
    Delivery,
    Order,
    OrderStatus,
    Tenant,
    TenantStatus,
    WhatsAppAccount,
)


@pytest.fixture
def escenario(monkeypatch):
    """Empresa con WhatsApp cargado, un cliente y un pedido de 150.000 Gs."""
    enviados = []

    async def falso_enviar(credenciales, para, texto):
        enviados.append((credenciales.phone_number_id, para, texto))
        return {}

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with Sesion() as db:
        tenant = Tenant(name="Pizzería", slug="pizzeria", status=TenantStatus.ACTIVE)
        db.add(tenant)
        db.flush()
        cliente = Customer(tenant_id=tenant.id, phone="595981777888", name="Ana")
        db.add(cliente)
        db.add(WhatsAppAccount(tenant_id=tenant.id, phone_number_id="123123123",
                               access_token_cifrado=cifrar("TOKEN")))
        db.flush()
        pedido = Order(tenant_id=tenant.id, customer_id=cliente.id,
                       status=OrderStatus.CONFIRMED, subtotal=150000, total=150000)
        db.add(pedido)
        db.commit()
        datos = {"pedido": pedido.id, "tenant": tenant.id}

    monkeypatch.setattr(notificaciones, "SessionLocal", Sesion)
    monkeypatch.setattr(notificaciones, "enviar_texto", falso_enviar)
    return {"sesion": Sesion, "enviados": enviados, **datos}


def avisar(escenario, estado):
    asyncio.run(notificaciones.avisar_cambio_de_estado(escenario["pedido"], estado, "v21.0"))


def test_el_cliente_recibe_el_aviso_de_confirmacion(escenario):
    avisar(escenario, OrderStatus.CONFIRMED)
    (numero, para, texto) = escenario["enviados"][0]
    assert numero == "123123123"          # sale por el número de esa empresa
    assert para == "595981777888"
    assert "150.000 Gs" in texto


def test_cada_estado_tiene_su_mensaje(escenario):
    for estado in (OrderStatus.PREPARING, OrderStatus.IN_TRANSIT, OrderStatus.DELIVERED,
                   OrderStatus.CANCELLED):
        avisar(escenario, estado)
    textos = [t for _n, _p, t in escenario["enviados"]]
    assert len(textos) == 4
    assert len(set(textos)) == 4, "los cuatro avisos deberían decir cosas distintas"


def test_estados_internos_no_molestan_al_cliente(escenario):
    """Al cliente no le importa que el pedido pase a DRAFT o a PENDING_PAYMENT."""
    avisar(escenario, OrderStatus.DRAFT)
    avisar(escenario, OrderStatus.PENDING_CONFIRMATION)
    assert escenario["enviados"] == []


def test_sin_whatsapp_cargado_no_se_avisa(escenario, monkeypatch):
    from sqlalchemy import select

    with escenario["sesion"]() as db:
        cuenta = db.scalar(select(WhatsAppAccount))
        cuenta.active = False
        db.commit()
    avisar(escenario, OrderStatus.CONFIRMED)
    assert escenario["enviados"] == []


def test_si_meta_falla_el_aviso_no_rompe_nada(escenario, monkeypatch):
    """El pedido ya cambió de estado: un fallo del aviso no debe propagarse."""
    async def explota(*_a, **_kw):
        raise RuntimeError("Meta dijo que no")

    monkeypatch.setattr(notificaciones, "enviar_texto", explota)
    avisar(escenario, OrderStatus.CONFIRMED)   # no debe levantar excepción


def test_el_aviso_de_en_camino_lleva_el_link_si_hay_url_publica(escenario, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ventasbot.com.py/")
    with escenario["sesion"]() as db:
        db.add(Delivery(tenant_id=escenario["tenant"], order_id=escenario["pedido"],
                        tracking_token="token-de-prueba-123"))
        db.commit()

    avisar(escenario, OrderStatus.IN_TRANSIT)
    texto = escenario["enviados"][0][2]
    assert "https://ventasbot.com.py/seguimiento/token-de-prueba-123" in texto


def test_sin_url_publica_el_aviso_sale_igual_pero_sin_link(escenario, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    avisar(escenario, OrderStatus.IN_TRANSIT)
    texto = escenario["enviados"][0][2]
    assert "camino" in texto
    assert "http" not in texto
