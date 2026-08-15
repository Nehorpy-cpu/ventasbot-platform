"""CRM conversacional multiempresa y canal oficial de WhatsApp."""

from __future__ import annotations

import json
import logging
import os
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .mensajes import MensajeEntrante, extraer_mensajes
from .models import (
    Conversation,
    ConversationMessage,
    ConversationStatus,
    Customer,
    MessageDirection,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethodConfig,
    PaymentStatus,
    Product,
    WhatsappIntegration,
    utcnow,
)
from .services import change_order_status
from .payment_gateway import create_bancard_checkout
from .sales_agent import run_commercial_agent

log = logging.getLogger("ventasbot.crm")


def _catalog_order(db: Session, conversation: Conversation, incoming: MensajeEntrante) -> str:
    requested: dict[str, int] = {}
    for item in incoming.datos.get("product_items") or []:
        sku = str(item.get("product_retailer_id") or "").strip()
        try:
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if sku and quantity > 0:
            requested[sku] = requested.get(sku, 0) + quantity
    if not requested:
        return "No pude leer los productos del carrito. Volvé a abrir el catálogo o escribí *asesor*."
    products = db.scalars(select(Product).where(
        Product.tenant_id == conversation.tenant_id,
        Product.sku.in_(requested),
        Product.active.is_(True),
    )).all()
    by_sku = {product.sku: product for product in products}
    unavailable = [sku for sku in requested if sku not in by_sku]
    unavailable += [product.name for product in products if product.stock < requested[product.sku]]
    if unavailable:
        return f"Hay productos no disponibles ({', '.join(unavailable)}). Actualizá el carrito o escribí *asesor*."
    order = Order(tenant_id=conversation.tenant_id, customer_id=conversation.customer_id,
                  status=OrderStatus.DRAFT, source="whatsapp_catalog")
    total = 0
    for sku, quantity in requested.items():
        product = by_sku[sku]
        subtotal = product.price * quantity
        total += subtotal
        order.items.append(OrderItem(product_id=product.id, product_name=product.name,
                                     unit_price=product.price, quantity=quantity, subtotal=subtotal))
    order.subtotal = order.total = total
    db.add(order)
    db.flush()
    conversation.active_order_id = order.id
    conversation.bot_state = "WAITING_LOCATION"
    return (f"Armé tu pedido #{order.id[-6:]} por Gs. {total:,}. ".replace(",", ".") +
            "Ahora enviame tu *ubicación de WhatsApp* para coordinar la entrega.")


def _continue_checkout(db: Session, conversation: Conversation, incoming: MensajeEntrante) -> str | None:
    if not conversation.active_order_id:
        return None
    order = db.scalar(select(Order).where(Order.id == conversation.active_order_id,
                                          Order.tenant_id == conversation.tenant_id))
    if not order:
        conversation.active_order_id = None
        conversation.bot_state = "START"
        return None
    if conversation.bot_state == "WAITING_LOCATION":
        if incoming.tipo != "location":
            return "Para seguir usá 📎 → *Ubicación* y enviame dónde entregar."
        latitude, longitude = str(incoming.datos.get("latitude") or ""), str(incoming.datos.get("longitude") or "")
        if not latitude or not longitude:
            return "La ubicación llegó incompleta. Por favor enviala nuevamente."
        order.latitude, order.longitude = latitude, longitude
        order.address = incoming.datos.get("address") or incoming.datos.get("name") or "Ubicación compartida"
        customer = db.get(Customer, conversation.customer_id)
        customer.latitude, customer.longitude, customer.address = order.latitude, order.longitude, order.address
        conversation.bot_state = "WAITING_SLOT"
        return "¡Ubicación recibida! ¿En qué horario querés recibir? Ejemplo: *hoy de 19:00 a 20:00*."
    if conversation.bot_state == "WAITING_SLOT":
        if not incoming.texto.strip():
            return "Decime por texto el día y rango horario en que podés recibir."
        order.requested_slot = incoming.texto.strip()[:80]
        order.status = OrderStatus.PENDING_CONFIRMATION
        conversation.bot_state = "WAITING_INVOICE"
        return ("¿Necesitás factura con datos? Enviá *Nombre o Razón Social, RUC* "
                "(ejemplo: ACME SA, 80012345-6) o escribí *consumidor final*.")
    if conversation.bot_state == "WAITING_INVOICE":
        answer = incoming.texto.strip()
        if not answer:
            return "Enviá Nombre/Razón Social y RUC, o escribí *consumidor final*."
        customer = db.get(Customer, conversation.customer_id)
        if answer.lower() not in {"consumidor final", "no", "sin factura"}:
            ruc = re.search(r"\b\d{5,12}(?:-\d)?\b", answer)
            if not ruc:
                return "No pude identificar el RUC. Usá: *Razón Social, 80012345-6*."
            customer.tax_id = ruc.group(0)
            billing_name = answer[:ruc.start()].strip(" ,-:")
            if billing_name:
                customer.name = billing_name[:160]
        methods = db.scalars(select(PaymentMethodConfig).where(
            PaymentMethodConfig.tenant_id == conversation.tenant_id,
            PaymentMethodConfig.enabled.is_(True),
        ).order_by(PaymentMethodConfig.display_name)).all()
        codes = {method.code for method in methods} or {"CASH_ON_DELIVERY"}
        labels = [method.display_name for method in methods] or ["Pago al recibir"]
        conversation.bot_state, conversation.bot_context = "WAITING_PAYMENT", ",".join(sorted(codes))
        return "Elegí cómo pagar: " + ", ".join(labels) + ". Escribí tu opción."
    if conversation.bot_state == "WAITING_PAYMENT":
        choice, enabled = incoming.texto.strip().lower(), set(filter(None, conversation.bot_context.split(",")))
        aliases = {
            "CASH_ON_DELIVERY": ("recibir", "efectivo", "contra entrega", "pos"),
            "BANK_TRANSFER": ("transferencia", "transferir"),
            "BANCARD": ("bancard", "tarjeta", "credito", "crédito", "debito", "débito"),
        }
        method = next((code for code, words in aliases.items()
                       if code in enabled and any(word in choice for word in words)), None)
        if not method:
            return "No reconocí esa forma de pago. Elegí una de las opciones habilitadas."
        order.payment_method = method
        if method == "CASH_ON_DELIVERY":
            change_order_status(db, order, OrderStatus.CONFIRMED, None)
        else:
            order.status = OrderStatus.PENDING_PAYMENT
        conversation.bot_state = "ORDER_CAPTURED"
        suffix = ("Tu pedido quedó confirmado." if method == "CASH_ON_DELIVERY" else
                  "Reservamos tu pedido; te enviaremos el enlace o las instrucciones seguras de pago.")
        return (f"✅ Pedido #{order.id[-6:]} capturado. {suffix} "
                "El depósito ya puede prepararlo y recibirás el tracking al asignar el delivery.")
    return None


def phone_number_id_from_payload(payload: dict) -> str:
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            metadata = (change.get("value") or {}).get("metadata") or {}
            if metadata.get("phone_number_id"):
                return str(metadata["phone_number_id"])
    return ""


def get_or_create_customer(db: Session, tenant_id: str, incoming: MensajeEntrante) -> Customer:
    customer = db.scalar(select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.phone == incoming.de,
    ))
    if customer:
        if incoming.nombre and not customer.name:
            customer.name = incoming.nombre
        return customer
    customer = Customer(tenant_id=tenant_id, phone=incoming.de, name=incoming.nombre)
    db.add(customer)
    db.flush()
    return customer


def get_or_create_conversation(db: Session, tenant_id: str, customer_id: str) -> Conversation:
    conversation = db.scalar(select(Conversation).where(
        Conversation.tenant_id == tenant_id,
        Conversation.customer_id == customer_id,
    ))
    if conversation:
        return conversation
    conversation = Conversation(tenant_id=tenant_id, customer_id=customer_id)
    db.add(conversation)
    db.flush()
    return conversation


def save_message(db: Session, *, tenant_id: str, conversation_id: str,
                 direction: MessageDirection, message_type: str, text: str,
                 external_id: str | None = None, payload: dict | None = None) -> ConversationMessage:
    message = ConversationMessage(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        external_id=external_id or None,
        direction=direction,
        message_type=message_type,
        text=text,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(message)
    return message


def bot_reply(db: Session, conversation: Conversation, incoming: MensajeEntrante) -> str | None:
    if conversation.status != ConversationStatus.BOT:
        return None
    if incoming.tipo == "order":
        return _catalog_order(db, conversation, incoming)
    checkout_reply = _continue_checkout(db, conversation, incoming)
    if checkout_reply is not None:
        return checkout_reply
    text = incoming.texto.strip().lower()
    if any(word in text for word in ("catálogo", "catalogo", "productos", "comprar")):
        products = db.scalars(select(Product).where(
            Product.tenant_id == conversation.tenant_id,
            Product.active.is_(True),
            Product.stock > 0,
        ).order_by(Product.name).limit(8)).all()
        if not products:
            return "El catálogo todavía se está preparando. Si querés, escribí *asesor* y te atiende una persona."
        lines = ["Estos son algunos productos disponibles:"]
        for product in products:
            lines.append(f"• {product.name} — Gs. {product.price:,}".replace(",", "."))
        lines.append("\nDecime el producto y la cantidad, o escribí *asesor*.")
        conversation.bot_state = "BROWSING_CATALOG"
        return "\n".join(lines)
    agent_reply = run_commercial_agent(db, conversation, incoming)
    if agent_reply is not None:
        return agent_reply
    if conversation.bot_state == "WAITING_AGENT_APPROVAL":
        return None
    if not text:
        return "Recibí tu mensaje. Por ahora podés escribir *catálogo* o *asesor*."
    return "¡Hola! Puedo ayudarte a comprar. Escribí *catálogo* para ver productos o *asesor* para hablar con una persona."


async def send_whatsapp_text(integration: WhatsappIntegration, to: str, text: str) -> str | None:
    if not integration.active or not integration.access_token_env:
        return None
    token = os.getenv(integration.access_token_env, "")
    if not token:
        log.error("Falta variable de token para tenant=%s", integration.tenant_id)
        return None
    url = f"https://graph.facebook.com/{integration.graph_version}/{integration.phone_number_id}/messages"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        })
        response.raise_for_status()
        data = response.json()
        messages = data.get("messages") or []
        return messages[0].get("id") if messages else None


async def _append_payment_instructions(db: Session, conversation: Conversation, reply: str) -> str:
    if conversation.bot_state != "ORDER_CAPTURED" or not conversation.active_order_id:
        return reply
    order = db.get(Order, conversation.active_order_id)
    if not order or order.payment_method == "CASH_ON_DELIVERY":
        return reply
    config = db.scalar(select(PaymentMethodConfig).where(
        PaymentMethodConfig.tenant_id == conversation.tenant_id,
        PaymentMethodConfig.code == order.payment_method,
        PaymentMethodConfig.enabled.is_(True),
    ))
    if not config:
        return reply + " El medio elegido dejó de estar disponible; un asesor continuará el cobro."
    if order.payment_method == "BANK_TRANSFER":
        return reply + (f"\n\n{config.instructions}" if config.instructions else "")
    if order.payment_method == "BANCARD":
        key = f"whatsapp-{order.id}"
        payment = db.scalar(select(Payment).where(
            Payment.tenant_id == order.tenant_id, Payment.idempotency_key == key))
        if payment and payment.checkout_url:
            return reply + f"\n\nPagá con Bancard acá: {payment.checkout_url}"
        if not payment:
            payment = Payment(tenant_id=order.tenant_id, order_id=order.id, provider="BANCARD",
                              status=PaymentStatus.PENDING, amount=order.total, idempotency_key=key)
            db.add(payment)
            db.flush()
        try:
            session = await create_bancard_checkout(config, order)
        except Exception:
            log.exception("No se pudo crear Tpago para order=%s", order.id)
            payment.status = PaymentStatus.REJECTED
            return reply + " No pudimos generar el enlace ahora; el equipo continuará el cobro sin perder tu pedido."
        payment.external_id, payment.checkout_url = session.external_id, session.checkout_url
        return reply + f"\n\nPagá con Bancard acá: {session.checkout_url}"
    return reply


async def process_meta_payload(payload: dict) -> None:
    with SessionLocal() as db:
        for incoming in extraer_mensajes(payload):
            if not incoming.phone_number_id:
                log.warning("Mensaje Meta sin phone_number_id: %s", incoming.id)
                continue
            integration = db.scalar(select(WhatsappIntegration).where(
                WhatsappIntegration.phone_number_id == incoming.phone_number_id,
                WhatsappIntegration.active.is_(True),
            ))
            if not integration:
                log.warning("Webhook para phone_number_id no registrado: %s", incoming.phone_number_id)
                continue
            if incoming.id and db.scalar(select(ConversationMessage.id).where(
                ConversationMessage.external_id == incoming.id
            )):
                continue
            customer = get_or_create_customer(db, integration.tenant_id, incoming)
            conversation = get_or_create_conversation(db, integration.tenant_id, customer.id)
            save_message(
                db,
                tenant_id=integration.tenant_id,
                conversation_id=conversation.id,
                direction=MessageDirection.INBOUND,
                message_type=incoming.tipo,
                text=incoming.texto,
                external_id=incoming.id,
                payload={"timestamp": incoming.timestamp, "content": incoming.datos},
            )
            conversation.unread_count += 1
            conversation.last_message_at = utcnow()
            reply = bot_reply(db, conversation, incoming)
            if reply:
                reply = await _append_payment_instructions(db, conversation, reply)
            db.commit()
            if reply:
                try:
                    external_id = await send_whatsapp_text(integration, incoming.de, reply)
                except Exception:
                    log.exception("No se pudo enviar respuesta tenant=%s", integration.tenant_id)
                    external_id = None
                save_message(
                    db,
                    tenant_id=integration.tenant_id,
                    conversation_id=conversation.id,
                    direction=MessageDirection.OUTBOUND,
                    message_type="text",
                    text=reply,
                    external_id=external_id,
                )
                conversation.last_message_at = utcnow()
                db.commit()
