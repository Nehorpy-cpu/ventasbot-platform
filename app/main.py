"""Webhook de WhatsApp Cloud API.

GET  /webhook  -> handshake de verificación de Meta (devuelve hub.challenge)
POST /webhook  -> recepción de mensajes, con validación de firma
GET  /salud    -> chequeo de configuración
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import cargar
from .ia import generar_respuesta
from .mensajes import MensajeEntrante, enviar_texto, extraer_mensajes
from .security import firma_valida
from .api import router as api_router
from .database import create_schema

log = logging.getLogger("whatsapp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema()
    yield


app = FastAPI(title="VentasBot Platform API", lifespan=lifespan)
cfg = cargar()
app.include_router(api_router)
static_dir = Path(__file__).with_name("static")
if static_dir.exists():
    app.mount("/panel", StaticFiles(directory=static_dir, html=True), name="panel")


@app.get("/salud")
def salud() -> dict[str, object]:
    faltan = cfg.faltantes()
    return {"ok": not faltan, "faltan": faltan, "graph": cfg.graph_version}


@app.get("/webhook", response_class=PlainTextResponse)
def verificar(
    modo: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Handshake. Meta espera el challenge crudo, en texto plano, con 200.

    Devolverlo como JSON (entre comillas) hace fallar la verificación.
    """
    if modo == "subscribe" and token and token == cfg.verify_token:
        log.info("Webhook verificado por Meta")
        return PlainTextResponse(challenge or "")
    log.warning("Handshake rechazado (modo=%s, token coincide=%s)", modo, token == cfg.verify_token)
    return Response(status_code=403)


@app.post("/webhook")
async def recibir(request: Request, tareas: BackgroundTasks) -> Response:
    """Valida la firma, encola el procesamiento y responde 200 enseguida.

    Meta reintenta si el endpoint tarda o no devuelve 200, y los reintentos
    duplican mensajes. Por eso el trabajo real va a background.
    """
    crudo = await request.body()

    if not firma_valida(crudo, request.headers.get("X-Hub-Signature-256"), cfg.app_secret):
        log.warning("Firma inválida: petición descartada")
        return Response(status_code=403)

    payload = await request.json()
    for mensaje in extraer_mensajes(payload):
        tareas.add_task(procesar, mensaje)

    return Response(status_code=200)


async def procesar(mensaje: MensajeEntrante) -> None:
    """Responde con el modelo local (Ollama). Ver app/ia.py."""
    log.info("Mensaje de %s (%s): %r", mensaje.de, mensaje.tipo, mensaje.texto)

    if mensaje.tipo != "text":
        respuesta = "Por ahora solo entiendo texto."
    else:
        respuesta = await generar_respuesta(cfg, mensaje)

    try:
        await enviar_texto(cfg, mensaje.de, respuesta)
    except Exception:
        # No relanzar: ya se respondió 200 a Meta y un fallo acá no debe
        # provocar reintentos ni tumbar el proceso.
        log.exception("No se pudo responder a %s", mensaje.de)
