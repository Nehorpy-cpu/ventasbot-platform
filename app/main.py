"""Webhook de WhatsApp Cloud API.

GET  /webhook  -> handshake de verificación de Meta (devuelve hub.challenge)
POST /webhook  -> recepción de mensajes, con validación de firma
GET  /salud    -> chequeo de configuración
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api import router as api_router
from .config import cargar
from .database import SessionLocal, engine
from .ia import ContextoTenant, generar_respuesta
from .mensajes import MensajeEntrante, enviar_texto, extraer_mensajes
from .models import Customer, Product, Tenant
from .security import firma_valida
from .whatsapp import credenciales_de_cuenta, cuenta_por_numero

log = logging.getLogger("whatsapp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Tablas que tienen que existir sí o sí. El esquema lo maneja Alembic, no la
# app: crear tablas al vuelo tapaba el hecho de que faltaba correr la migración.
TABLAS_MINIMAS = {"tenants", "users", "whatsapp_accounts"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    faltantes = TABLAS_MINIMAS - set(inspect(engine).get_table_names())
    if faltantes:
        raise RuntimeError(
            f"La base no está migrada (faltan: {', '.join(sorted(faltantes))}). "
            "Corré: alembic upgrade head"
        )
    yield


app = FastAPI(title="VentasBot Platform API", lifespan=lifespan)
cfg = cargar()
app.include_router(api_router)
static_dir = Path(__file__).with_name("static")
if static_dir.exists():
    app.mount("/panel", StaticFiles(directory=static_dir, html=True), name="panel")


# Meta reintenta el mismo webhook si no le llega el 200 a tiempo, y el
# reintento trae los mismos message.id. Sin esto el bot contesta dos veces.
MAX_IDS_RECORDADOS = 5000
_ids_procesados: OrderedDict[str, None] = OrderedDict()


def ya_procesado(id_mensaje: str) -> bool:
    """True si este message.id ya se atendió. Registra el id de paso."""
    if not id_mensaje:
        return False
    if id_mensaje in _ids_procesados:
        return True
    _ids_procesados[id_mensaje] = None
    while len(_ids_procesados) > MAX_IDS_RECORDADOS:
        _ids_procesados.popitem(last=False)
    return False


@app.get("/salud")
def salud() -> dict[str, object]:
    faltan = cfg.faltantes()
    try:
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
        base_ok = True
    except Exception:
        log.exception("La base de datos no responde")
        base_ok = False
    return {
        "ok": not faltan and base_ok,
        "faltan": faltan,
        "base_datos": "ok" if base_ok else "sin conexión",
        "graph": cfg.graph_version,
    }


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

    try:
        payload = json.loads(crudo)
    except ValueError:
        # Firma válida pero cuerpo ilegible: se avisa y se corta acá. Devolver
        # 200 igual, porque reintentar no va a arreglar un JSON roto.
        log.warning("Cuerpo del webhook no es JSON válido")
        return Response(status_code=200)

    for mensaje in extraer_mensajes(payload):
        if ya_procesado(mensaje.id):
            log.info("Mensaje %s repetido (reintento de Meta): se ignora", mensaje.id)
            continue
        tareas.add_task(procesar, mensaje)

    return Response(status_code=200)


async def procesar(mensaje: MensajeEntrante) -> None:
    """Atiende un mensaje entrante con las credenciales de la empresa dueña.

    El ruteo es por `phone_number_id`: es el número de la plataforma al que
    llegó el mensaje, y cada empresa carga el suyo. Si nadie lo reclama, no se
    contesta — usar las credenciales de otra empresa sería peor que el silencio.
    """
    log.info("Mensaje de %s para el numero %s (%s)", mensaje.de, mensaje.phone_number_id, mensaje.tipo)

    with SessionLocal() as db:
        cuenta = cuenta_por_numero(db, mensaje.phone_number_id)
        if cuenta is None:
            log.warning(
                "Ninguna empresa activa tiene cargado el numero %s: mensaje descartado",
                mensaje.phone_number_id,
            )
            return
        tenant_id = cuenta.tenant_id
        try:
            credenciales = credenciales_de_cuenta(cuenta, cfg.graph_version)
        except RuntimeError:
            log.exception("No se pudieron leer las credenciales de la empresa %s", tenant_id)
            return
        contexto = contexto_de_tenant(db, tenant_id)
        registrar_contacto(db, tenant_id, mensaje)

    if mensaje.tipo != "text":
        respuesta = "Por ahora solo entiendo texto."
    else:
        respuesta = await generar_respuesta(cfg, mensaje, contexto)

    try:
        await enviar_texto(credenciales, mensaje.de, respuesta)
    except Exception:
        # No relanzar: ya se respondió 200 a Meta y un fallo acá no debe
        # provocar reintentos ni tumbar el proceso.
        log.exception("No se pudo responder a %s", mensaje.de)


def contexto_de_tenant(db: Session, tenant_id: str) -> ContextoTenant | None:
    """Catálogo y nombre de la empresa para que el modelo no invente."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return None
    productos = db.scalars(
        select(Product)
        .where(Product.tenant_id == tenant_id, Product.active.is_(True))
        .order_by(Product.name)
    ).all()
    return ContextoTenant(
        nombre_empresa=tenant.name,
        moneda=tenant.currency,
        productos=[(p.name, p.price, p.stock) for p in productos],
    )


def registrar_contacto(db: Session, tenant_id: str, mensaje: MensajeEntrante) -> None:
    """Deja al que escribe fichado como cliente de esa empresa.

    Si ya existía no se pisa el nombre cargado a mano por el vendedor con el
    nombre de perfil de WhatsApp, que el cliente puede cambiar cuando quiera.
    """
    if not mensaje.de:
        return
    existe = db.scalar(select(Customer).where(
        Customer.tenant_id == tenant_id, Customer.phone == mensaje.de))
    if existe:
        return
    db.add(Customer(tenant_id=tenant_id, phone=mensaje.de, name=mensaje.nombre or ""))
    try:
        db.commit()
    except IntegrityError:
        # Dos mensajes del mismo número casi a la vez: el primero ya lo creó.
        db.rollback()
