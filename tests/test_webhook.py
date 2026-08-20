"""Webhook: handshake, firma, parseo y ruteo multiempresa por número."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.cripto import cifrar
from app.database import Base
from app.mensajes import cuerpo_texto, extraer_mensajes
from app.models import Product, Tenant, TenantStatus, WhatsAppAccount
from app.security import firmar

SECRETO = "secreto-de-prueba"
VERIFY = "token-de-prueba"

# Números de la plataforma: cada empresa carga el suyo. Meta los manda en
# value.metadata.phone_number_id y con eso se resuelve de quién es el mensaje.
PNID_PIZZERIA = "111111111111111"
PNID_FARMACIA = "222222222222222"
PNID_HUERFANO = "999999999999999"  # nadie lo cargó


@pytest.fixture
def cliente(monkeypatch):
    """TestClient con base propia y el envío a Meta anulado.

    `procesar` abre su propia sesión (corre en background, sin Depends), así
    que no alcanza con dependency_overrides: hay que cambiarle el SessionLocal.
    """
    enviados = []

    async def falso_enviar(credenciales, para, texto):
        enviados.append((credenciales.phone_number_id, para, texto))
        return {"messages": [{"id": "wamid.fake"}]}

    async def falsa_ia(cfg, mensaje, contexto=None):
        empresa = contexto.nombre_empresa if contexto else "sin empresa"
        return f"[{empresa}] Recibí: {mensaje.texto}"

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with Sesion() as db:
        pizzeria = Tenant(name="Pizzería Central", slug="pizzeria-central", status=TenantStatus.ACTIVE)
        farmacia = Tenant(name="Farmacia Norte", slug="farmacia-norte", status=TenantStatus.ACTIVE)
        db.add_all([pizzeria, farmacia])
        db.flush()
        db.add_all([
            WhatsAppAccount(tenant_id=pizzeria.id, phone_number_id=PNID_PIZZERIA,
                            access_token_cifrado=cifrar("TOKEN-PIZZERIA")),
            WhatsAppAccount(tenant_id=farmacia.id, phone_number_id=PNID_FARMACIA,
                            access_token_cifrado=cifrar("TOKEN-FARMACIA")),
            Product(tenant_id=pizzeria.id, sku="MUZZA", name="Pizza muzzarella", price=35000, stock=12),
        ])
        db.commit()
        ids = {"pizzeria": pizzeria.id, "farmacia": farmacia.id}

    monkeypatch.setattr(main, "SessionLocal", Sesion)
    monkeypatch.setattr(main, "enviar_texto", falso_enviar)
    monkeypatch.setattr(main, "generar_respuesta", falsa_ia)
    main._ids_procesados.clear()
    with TestClient(main.app) as c:
        c.enviados = enviados
        c.sesion = Sesion
        c.tenants = ids
        yield c
    main._ids_procesados.clear()


def post_firmado(cliente, payload: dict, secreto: str = SECRETO):
    crudo = json.dumps(payload).encode()
    return cliente.post(
        "/webhook",
        content=crudo,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firmar(crudo, secreto),
        },
    )


def payload_texto(phone_number_id: str = PNID_PIZZERIA, texto: str = "hola bot",
                  wamid: str = "wamid.ABC", de: str = "595981123456") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "595981000000",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"wa_id": de, "profile": {"name": "Ana"}}],
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": de,
                                    "timestamp": "1754900000",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


PAYLOAD_TEXTO = payload_texto()

PAYLOAD_STATUS = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": PNID_PIZZERIA},
                        "statuses": [
                            {"id": "wamid.XYZ", "status": "delivered", "recipient_id": "595981123456"}
                        ],
                    },
                }
            ],
        }
    ],
}


# --- handshake GET ---------------------------------------------------------

def test_handshake_devuelve_el_challenge_en_texto_plano(cliente):
    r = cliente.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert r.status_code == 200
    assert r.text == "1158201444"                      # crudo, sin comillas
    assert r.headers["content-type"].startswith("text/plain")


def test_handshake_con_token_incorrecto_da_403(cliente):
    r = cliente.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "123"},
    )
    assert r.status_code == 403


def test_handshake_sin_modo_subscribe_da_403(cliente):
    r = cliente.get(
        "/webhook",
        params={"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "123"},
    )
    assert r.status_code == 403


# --- firma POST ------------------------------------------------------------

def test_post_con_firma_valida_acepta_y_responde(cliente):
    r = post_firmado(cliente, PAYLOAD_TEXTO)
    assert r.status_code == 200
    # Responde con el número de la pizzería, no con uno global.
    assert cliente.enviados == [(PNID_PIZZERIA, "595981123456", "[Pizzería Central] Recibí: hola bot")]


def test_post_con_firma_invalida_da_403_y_no_procesa(cliente):
    r = post_firmado(cliente, PAYLOAD_TEXTO, secreto="secreto-equivocado")
    assert r.status_code == 403
    assert cliente.enviados == []


def test_post_sin_cabecera_de_firma_da_403(cliente):
    r = cliente.post("/webhook", json=PAYLOAD_TEXTO)
    assert r.status_code == 403
    assert cliente.enviados == []


def test_firma_sobre_json_reserializado_no_coincide():
    """El error clásico: firmar el dict re-serializado en vez de los bytes crudos."""
    crudo = b'{"a":1,  "b":2}'
    reserializado = json.dumps(json.loads(crudo)).encode()
    assert crudo != reserializado
    assert firmar(crudo, SECRETO) != firmar(reserializado, SECRETO)


# --- parseo del payload ----------------------------------------------------

def test_extrae_el_mensaje_de_texto_con_nombre_de_perfil():
    (m,) = extraer_mensajes(PAYLOAD_TEXTO)
    assert (m.de, m.tipo, m.texto, m.nombre) == ("595981123456", "text", "hola bot", "Ana")


def test_los_acuses_de_entrega_no_son_mensajes():
    assert extraer_mensajes(PAYLOAD_STATUS) == []


def test_payload_vacio_no_revienta():
    assert extraer_mensajes({}) == []


def test_los_acuses_no_disparan_respuesta(cliente):
    r = post_firmado(cliente, PAYLOAD_STATUS)
    assert r.status_code == 200
    assert cliente.enviados == []


def test_mensaje_no_texto_no_pierde_el_remitente():
    payload = json.loads(json.dumps(PAYLOAD_TEXTO))
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.IMG", "from": "595981123456", "timestamp": "1754900001", "type": "image",
        "image": {"id": "media-1"},
    }
    (m,) = extraer_mensajes(payload)
    assert (m.tipo, m.texto, m.de) == ("image", "", "595981123456")


# --- envío -----------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada", ["+595 981 123-456", "595981123456", "+595981123456"]
)
def test_el_numero_se_normaliza_sin_mas_ni_espacios(entrada):
    assert cuerpo_texto(entrada, "hola")["to"] == "595981123456"


def test_el_cuerpo_lleva_messaging_product():
    cuerpo = cuerpo_texto("595981123456", "hola")
    assert cuerpo["messaging_product"] == "whatsapp"
    assert cuerpo["text"]["body"] == "hola"


# --- salud -----------------------------------------------------------------

def test_salud_reporta_ok_con_la_config_completa(cliente):
    assert cliente.get("/salud").json()["ok"] is True


def test_reintento_de_meta_no_duplica_la_respuesta(cliente):
    """Meta reenvía el mismo message.id si no le llegó el 200: no contestar dos veces."""
    main._ids_procesados.clear()

    assert post_firmado(cliente, PAYLOAD_TEXTO).status_code == 200
    assert len(cliente.enviados) == 1

    assert post_firmado(cliente, PAYLOAD_TEXTO).status_code == 200
    assert len(cliente.enviados) == 1, "el reintento generó una segunda respuesta"

    main._ids_procesados.clear()


def test_cuerpo_ilegible_no_tumba_el_webhook(cliente):
    """Firma válida sobre bytes que no son JSON: se descarta con 200, sin 500."""
    main._ids_procesados.clear()
    crudo = b"{esto no es json"
    respuesta = cliente.post(
        "/webhook",
        content=crudo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firmar(crudo, SECRETO)},
    )
    assert respuesta.status_code == 200
    assert cliente.enviados == []


def test_recuerda_una_cantidad_acotada_de_ids(cliente):
    """El registro anti-duplicados no puede crecer para siempre en memoria."""
    main._ids_procesados.clear()
    for i in range(main.MAX_IDS_RECORDADOS + 50):
        main.ya_procesado(f"wamid.{i}")
    assert len(main._ids_procesados) <= main.MAX_IDS_RECORDADOS
    main._ids_procesados.clear()


# --- ruteo multiempresa ----------------------------------------------------

def test_cada_empresa_responde_con_su_propio_numero(cliente):
    """El mismo webhook atiende a las dos empresas, cada una con su token."""
    post_firmado(cliente, payload_texto(PNID_PIZZERIA, "quiero una muzza", "wamid.P1"))
    post_firmado(cliente, payload_texto(PNID_FARMACIA, "tenés ibuprofeno?", "wamid.F1"))

    numeros = [pnid for pnid, _para, _texto in cliente.enviados]
    assert numeros == [PNID_PIZZERIA, PNID_FARMACIA]
    assert "Pizzería Central" in cliente.enviados[0][2]
    assert "Farmacia Norte" in cliente.enviados[1][2]


def test_numero_que_ninguna_empresa_cargo_no_recibe_respuesta(cliente):
    """Contestar con las credenciales de otra empresa sería peor que el silencio."""
    r = post_firmado(cliente, payload_texto(PNID_HUERFANO, "hola", "wamid.H1"))
    assert r.status_code == 200
    assert cliente.enviados == []


def test_empresa_suspendida_no_contesta(cliente):
    from app.models import Tenant, TenantStatus

    with cliente.sesion() as db:
        tenant = db.get(Tenant, cliente.tenants["pizzeria"])
        tenant.status = TenantStatus.SUSPENDED
        db.commit()

    post_firmado(cliente, payload_texto(PNID_PIZZERIA, "hola", "wamid.S1"))
    assert cliente.enviados == []


def test_cuenta_desactivada_no_contesta(cliente):
    from sqlalchemy import select

    from app.models import WhatsAppAccount

    with cliente.sesion() as db:
        cuenta = db.scalar(select(WhatsAppAccount).where(
            WhatsAppAccount.phone_number_id == PNID_PIZZERIA))
        cuenta.active = False
        db.commit()

    post_firmado(cliente, payload_texto(PNID_PIZZERIA, "hola", "wamid.D1"))
    assert cliente.enviados == []


def test_el_que_escribe_queda_fichado_como_cliente_de_esa_empresa(cliente):
    """Un número que escribe por primera vez se guarda como Customer del tenant."""
    from sqlalchemy import select

    from app.models import Customer

    post_firmado(cliente, payload_texto(PNID_PIZZERIA, "hola", "wamid.C1", de="595971555444"))

    with cliente.sesion() as db:
        cliente_nuevo = db.scalar(select(Customer).where(Customer.phone == "595971555444"))
        assert cliente_nuevo is not None
        assert cliente_nuevo.tenant_id == cliente.tenants["pizzeria"]
        assert cliente_nuevo.name == "Ana"


def test_el_mensaje_lleva_a_que_numero_llego():
    (m,) = extraer_mensajes(payload_texto(PNID_FARMACIA))
    assert m.phone_number_id == PNID_FARMACIA
