"""Motor comercial seguro y auditable para conversaciones de WhatsApp.

El motor propone texto y decisiones estructuradas. Nunca cambia stock, pagos,
facturas ni estados de pedidos: esas operaciones siguen en los servicios de dominio.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .mensajes import MensajeEntrante
from .models import (
    AgentMode,
    AgentRun,
    AgentRunStatus,
    Conversation,
    ConversationStatus,
    Customer,
    PendingAgentAction,
    Product,
    SalesObjection,
    SalesPlaybook,
)


DEFAULT_ESCALATION_WORDS = "humano,asesor,reclamo,abogado,estafa,cancelar,denuncia"


@dataclass(frozen=True)
class CommercialDecision:
    intent: str
    confidence: int
    score: int
    temperature: str
    urgency: str | None
    budget: int | None
    objection_name: str | None
    suggested_reply: str
    decision: str
    escalated: bool


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


def split_terms(value: str) -> list[str]:
    return [normalize_text(term) for term in re.split(r"[,\n;]", value) if term.strip()]


def default_playbook(db: Session, tenant_id: str) -> SalesPlaybook:
    playbook = db.scalar(select(SalesPlaybook).where(SalesPlaybook.tenant_id == tenant_id))
    if playbook:
        return playbook
    playbook = SalesPlaybook(tenant_id=tenant_id, escalation_words=DEFAULT_ESCALATION_WORDS)
    db.add(playbook)
    db.flush()
    return playbook


def _budget(text: str) -> int | None:
    candidates = re.findall(r"(?:gs\.?|₲)?\s*(\d{2,3}(?:[. ]\d{3})+|\d{5,9})", text, flags=re.IGNORECASE)
    if not candidates:
        return None
    values = [int(re.sub(r"\D", "", candidate)) for candidate in candidates]
    return max(values) if values else None


def _temperature(score: int, playbook: SalesPlaybook) -> str:
    if score >= playbook.hot_threshold:
        return "HOT"
    if score >= playbook.warm_threshold:
        return "WARM"
    return "COLD"


def _find_objection(text: str, objections: list[SalesObjection]) -> SalesObjection | None:
    for objection in objections:
        if any(term in text for term in split_terms(objection.triggers)):
            return objection
    return None


def decide(db: Session, conversation: Conversation, incoming: MensajeEntrante,
           playbook: SalesPlaybook) -> CommercialDecision:
    text = normalize_text(incoming.texto)
    objections = db.scalars(select(SalesObjection).where(
        SalesObjection.tenant_id == conversation.tenant_id,
        SalesObjection.active.is_(True),
    ).order_by(SalesObjection.name)).all()
    customer = db.get(Customer, conversation.customer_id)
    escalation = next((term for term in split_terms(playbook.escalation_words) if term in text), None)
    budget = _budget(text)
    urgency = "HIGH" if any(term in text for term in ("hoy", "ahora", "urgente", "ya")) else (
        "MEDIUM" if any(term in text for term in ("manana", "esta semana", "pronto")) else None
    )
    intent = "UNKNOWN"
    score = 5
    confidence = 55
    if any(term in text for term in ("comprar", "quiero", "necesito", "pedido", "llevo")):
        intent, score, confidence = "BUY_PRODUCT", 50, 88
    elif any(term in text for term in ("precio", "cuanto", "costo", "sale")):
        intent, score, confidence = "ASK_PRICE", 35, 86
    elif any(term in text for term in ("catalogo", "productos", "opciones")):
        intent, score, confidence = "BROWSE_CATALOG", 30, 92
    if budget:
        score += 20
    if urgency == "HIGH":
        score += 20
    elif urgency == "MEDIUM":
        score += 10
    if any(term in text for term in ("tarjeta", "transferencia", "efectivo", "bancard")):
        score += 15
    objection = _find_objection(text, objections)
    if objection:
        intent, confidence, score = "HANDLE_OBJECTION", 96, max(score, 55)
    score = min(score, 100)
    temperature = _temperature(score, playbook)

    if escalation:
        return CommercialDecision(
            intent="HUMAN_HANDOFF", confidence=100, score=score, temperature=temperature,
            urgency=urgency, budget=budget, objection_name=None,
            suggested_reply="Entiendo. Te paso con una persona del equipo para que continúe desde acá.",
            decision="ESCALATE_TO_HUMAN", escalated=True,
        )
    if objection:
        return CommercialDecision(
            intent=intent, confidence=confidence, score=score, temperature=temperature,
            urgency=urgency, budget=budget, objection_name=objection.name,
            suggested_reply=objection.response, decision="REPLY_WITH_PLAYBOOK", escalated=False,
        )
    if intent == "ASK_PRICE":
        products = db.scalars(select(Product).where(
            Product.tenant_id == conversation.tenant_id, Product.active.is_(True), Product.stock > 0
        ).order_by(Product.name).limit(3)).all()
        if products:
            options = ", ".join(f"{product.name} a Gs. {product.price:,}".replace(",", ".") for product in products)
            reply = f"Estas son algunas opciones disponibles: {options}. ¿Cuál te interesa?"
        else:
            reply = "Decime qué producto te interesa y una persona del equipo confirma el precio."
        decision_name = "SHOW_PRICES"
    elif intent == "BUY_PRODUCT":
        reply, decision_name = "Perfecto. Escribí *catálogo* para elegir productos y cantidades.", "START_CHECKOUT"
    elif intent == "BROWSE_CATALOG":
        reply, decision_name = "Te muestro el catálogo disponible. Escribí *catálogo* para comenzar.", "SHOW_CATALOG"
    else:
        reply, decision_name = "", "NO_ACTION"
    return CommercialDecision(
        intent=intent, confidence=confidence, score=score, temperature=temperature,
        urgency=urgency, budget=budget, objection_name=None, suggested_reply=reply,
        decision=decision_name, escalated=False,
    )


def run_commercial_agent(db: Session, conversation: Conversation,
                         incoming: MensajeEntrante) -> str | None:
    """Evalúa un turno; devuelve texto sólo cuando está autorizado para salir."""
    if incoming.tipo != "text" or not incoming.texto.strip():
        return None
    playbook = default_playbook(db, conversation.tenant_id)
    if not playbook.enabled:
        return None
    outcome = decide(db, conversation, incoming, playbook)
    customer = db.get(Customer, conversation.customer_id)
    customer.lead_score = outcome.score
    customer.lead_temperature = outcome.temperature
    customer.purchase_intent = outcome.intent
    customer.estimated_budget = outcome.budget
    customer.urgency = outcome.urgency
    customer.next_action = outcome.decision

    requires_approval = bool(outcome.suggested_reply) and (
        playbook.mode == AgentMode.DRAFT or outcome.confidence < playbook.auto_send_min_confidence
    ) and not outcome.escalated
    status = AgentRunStatus.ESCALATED if outcome.escalated else (
        AgentRunStatus.PENDING_APPROVAL if requires_approval else AgentRunStatus.COMPLETED
    )
    steps = [
        {"n": 1, "name": "context", "status": "DONE"},
        {"n": 2, "name": "qualification", "status": "DONE"},
        {"n": 3, "name": "reply", "status": "PENDING" if requires_approval else ("DONE" if outcome.suggested_reply else "SKIPPED")},
        {"n": 4, "name": "checkout", "status": "DELEGATED" if outcome.decision == "START_CHECKOUT" else "SKIPPED"},
        {"n": 5, "name": "crm", "status": "DONE"},
        {"n": 6, "name": "handoff", "status": "DONE" if outcome.escalated else "SKIPPED"},
    ]
    run = AgentRun(
        tenant_id=conversation.tenant_id, conversation_id=conversation.id, customer_id=customer.id,
        input_text=incoming.texto, intent=outcome.intent, confidence=outcome.confidence,
        lead_score=outcome.score, temperature=outcome.temperature,
        objection_name=outcome.objection_name, suggested_reply=outcome.suggested_reply,
        decision=outcome.decision, status=status, steps_json=json.dumps(steps),
    )
    db.add(run)
    db.flush()
    if outcome.escalated:
        conversation.status = ConversationStatus.HUMAN
        conversation.bot_state = "WAITING_HUMAN"
        return outcome.suggested_reply
    if requires_approval:
        conversation.bot_state = "WAITING_AGENT_APPROVAL"
        db.add(PendingAgentAction(
            tenant_id=conversation.tenant_id, run_id=run.id, conversation_id=conversation.id,
            customer_id=customer.id, proposed_text=outcome.suggested_reply,
        ))
        return None
    return outcome.suggested_reply or None
