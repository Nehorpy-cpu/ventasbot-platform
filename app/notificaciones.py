"""Avisos por WhatsApp al cliente cuando su pedido cambia de estado.

Se mandan con las credenciales de la empresa dueña del pedido, en background,
y nunca hacen fallar la operación que los disparó: si el aviso no sale, el
pedido igual cambió de estado. El vendedor ve el estado en el panel; el
cliente, en el peor caso, no recibe el mensaje.

Límite conocido: `enviar_texto` solo funciona dentro de la ventana de 24 h
desde el último mensaje del cliente. Fuera de esa ventana Meta rechaza el
envío y hay que usar una plantilla aprobada — queda registrado en el log.
"""

from __future__ import annotations

import logging
import os

from .database import SessionLocal
from .mensajes import enviar_texto
from .models import Customer, Delivery, Order, OrderStatus
from .whatsapp import credenciales_de_cuenta, cuenta_de_tenant

log = logging.getLogger("whatsapp.avisos")

PLANTILLAS: dict[OrderStatus, str] = {
    OrderStatus.CONFIRMED: "¡Confirmamos tu pedido! Total: {total}. Ya lo estamos gestionando.",
    OrderStatus.PREPARING: "Estamos preparando tu pedido. Te avisamos cuando salga.",
    OrderStatus.READY: "Tu pedido ya está listo. En breve sale para tu dirección.",
    OrderStatus.ASSIGNED: "Un repartidor tomó tu pedido y sale en un rato.",
    OrderStatus.IN_TRANSIT: "¡Tu pedido va en camino!{seguimiento}",
    OrderStatus.DELIVERED: "Tu pedido fue entregado. ¡Gracias por comprarnos!",
    OrderStatus.CANCELLED: "Tu pedido fue cancelado. Si fue un error, escribinos y lo resolvemos.",
}


def _formato_guarani(monto: int) -> str:
    return f"{monto:,}".replace(",", ".") + " Gs"


def _link_seguimiento(db, order_id: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        return ""
    entrega = db.query(Delivery).filter(Delivery.order_id == order_id).first()
    if not entrega:
        return ""
    return f"\nSeguilo acá: {base}/seguimiento/{entrega.tracking_token}"


def armar_texto(db, pedido: Order, estado: OrderStatus) -> str | None:
    plantilla = PLANTILLAS.get(estado)
    if not plantilla:
        return None
    return plantilla.format(
        total=_formato_guarani(pedido.total),
        seguimiento=_link_seguimiento(db, pedido.id),
    )


async def avisar_cambio_de_estado(order_id: str, estado: OrderStatus, graph_version: str) -> None:
    """Corre en background después de que el cambio de estado ya se guardó."""
    try:
        with SessionLocal() as db:
            pedido = db.get(Order, order_id)
            if not pedido:
                return
            texto = armar_texto(db, pedido, estado)
            if not texto:
                return
            cliente = db.get(Customer, pedido.customer_id)
            if not cliente or not cliente.phone:
                log.info("Pedido %s sin teléfono de cliente: no se avisa", order_id)
                return
            cuenta = cuenta_de_tenant(db, pedido.tenant_id)
            if not cuenta or not cuenta.active:
                log.info("La empresa %s no tiene WhatsApp activo: no se avisa", pedido.tenant_id)
                return
            credenciales = credenciales_de_cuenta(cuenta, graph_version)
            telefono = cliente.phone
        await enviar_texto(credenciales, telefono, texto)
        log.info("Aviso de %s enviado para el pedido %s", estado.value, order_id)
    except Exception:
        # Fuera de la ventana de 24 h Meta rechaza el texto libre. No es un
        # error de la plataforma y no debe ensuciar la operación.
        log.exception("No se pudo avisar el cambio a %s del pedido %s", estado.value, order_id)
