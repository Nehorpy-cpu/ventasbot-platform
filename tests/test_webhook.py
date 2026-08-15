import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.mensajes import cuerpo_texto, extraer_mensajes
from app.security import firmar

SECRETO = "secreto-de-prueba"
VERIFY = "token-de-prueba"


@pytest.fixture
def cliente(monkeypatch):
    """TestClient con el envío a Meta anulado (los background tasks corren de verdad)."""
    enviados = []

    async def falso_enviar(cfg, para, texto):
        enviados.append((para, texto))
        return {"messages": [{"id": "wamid.fake"}]}

    monkeypatch.setattr(main, "enviar_texto", falso_enviar)
    with TestClient(main.app) as c:
        c.enviados = enviados
        yield c


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


PAYLOAD_TEXTO = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "123456789"},
                        "contacts": [{"wa_id": "595981123456", "profile": {"name": "Ana"}}],
                        "messages": [
                            {
                                "id": "wamid.ABC",
                                "from": "595981123456",
                                "timestamp": "1754900000",
                                "type": "text",
                                "text": {"body": "hola bot"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

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
    assert cliente.enviados == [("595981123456", "Recibí: hola bot")]


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
    assert m.phone_number_id == "123456789"


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


def test_extrae_ubicacion_y_pedido_de_catalogo():
    payload = json.loads(json.dumps(PAYLOAD_TEXTO))
    messages = payload["entry"][0]["changes"][0]["value"]["messages"]
    messages[:] = [
        {"id": "wamid.LOC", "from": "595981123456", "timestamp": "1", "type": "location",
         "location": {"latitude": -25.3, "longitude": -57.6, "name": "Casa"}},
        {"id": "wamid.ORDER", "from": "595981123456", "timestamp": "2", "type": "order",
         "order": {"catalog_id": "cat-1", "product_items": [
             {"product_retailer_id": "PIZZA-FAM", "quantity": "2", "item_price": "85000"}
         ]}},
    ]
    location, order = extraer_mensajes(payload)
    assert location.datos["latitude"] == -25.3
    assert order.datos["product_items"][0]["product_retailer_id"] == "PIZZA-FAM"


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
