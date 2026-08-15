"""Webhook de WhatsApp Cloud API.

GET  /webhook  -> handshake de verificación de Meta (devuelve hub.challenge)
POST /webhook  -> recepción de mensajes, con validación de firma
GET  /salud    -> chequeo de configuración
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import cargar
from .mensajes import MensajeEntrante, enviar_texto, extraer_mensajes
from .security import firma_valida
from .api import router as api_router
from .auth import validate_security_config
from .database import SessionLocal, create_schema
from .meta_webhook import router as meta_webhook_router
from .payment_webhook import router as payment_webhook_router

log = logging.getLogger("whatsapp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_security_config()
    if os.getenv("APP_ENV", "development").lower() not in {"prod", "production"}:
        create_schema()
    yield


app = FastAPI(title="VentasBot Platform API", lifespan=lifespan)
cfg = cargar()
legacy_webhook_enabled = os.getenv(
    "ENABLE_LEGACY_WEBHOOK", "0" if os.getenv("APP_ENV", "development").lower() in {"prod", "production"} else "1"
) == "1"
app.include_router(api_router)
app.include_router(meta_webhook_router)
app.include_router(payment_webhook_router)
static_dir = Path(__file__).with_name("static")
if static_dir.exists():
    app.mount("/panel", StaticFiles(directory=static_dir, html=True), name="panel")


@app.get("/salud")
def salud() -> dict[str, object]:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False
    faltan = cfg.faltantes() if legacy_webhook_enabled else []
    return {"ok": database_ok and not faltan, "database": database_ok,
            "legacy_faltan": faltan, "graph": cfg.graph_version}


@app.get("/webhook", response_class=PlainTextResponse)
def verificar(
    modo: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Handshake. Meta espera el challenge crudo, en texto plano, con 200.

    Devolverlo como JSON (entre comillas) hace fallar la verificación.
    """
    if not legacy_webhook_enabled:
        return Response(status_code=404)
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
    if not legacy_webhook_enabled:
        return Response(status_code=404)
    crudo = await request.body()

    if not firma_valida(crudo, request.headers.get("X-Hub-Signature-256"), cfg.app_secret):
        log.warning("Firma inválida: petición descartada")
        return Response(status_code=403)

    payload = await request.json()
    for mensaje in extraer_mensajes(payload):
        tareas.add_task(procesar, mensaje)

    return Response(status_code=200)


async def procesar(mensaje: MensajeEntrante) -> None:
    """Acá va la lógica del bot. Por ahora, eco."""
    log.info("Mensaje WhatsApp id=%s tipo=%s remitente=***%s", mensaje.id, mensaje.tipo, mensaje.de[-4:])

    if mensaje.tipo != "text":
        respuesta = "Por ahora solo entiendo texto."
    else:
        respuesta = f"Recibí: {mensaje.texto}"

    try:
        await enviar_texto(cfg, mensaje.de, respuesta)
    except Exception:
        # No relanzar: ya se respondió 200 a Meta y un fallo acá no debe
        # provocar reintentos ni tumbar el proceso.
        log.exception("No se pudo responder mensaje id=%s remitente=***%s", mensaje.id, mensaje.de[-4:])
