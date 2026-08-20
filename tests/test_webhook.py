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

    async def falsa_ia(cfg, mensaje):
        return f"Recibí: {mensaje.texto}"

    monkeypatch.setattr(main, "enviar_texto", falso_enviar)
    monkeypatch.setattr(main, "generar_respuesta", falsa_ia)
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
