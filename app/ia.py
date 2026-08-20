"""Generación de respuestas con el modelo de IA local (Ollama).

Hoy responde con una persona genérica de asistente de ventas, sin catálogo
de producto ni creación de pedidos automática: eso requiere resolver primero
a qué tenant pertenece cada número de WhatsApp entrante (el webhook actual
solo maneja un PHONE_NUMBER_ID global), que es una decisión de producto, no
técnica. Cuando se defina eso, este módulo es el lugar para inyectar el
catálogo del tenant correspondiente en el prompt.
"""

from __future__ import annotations

import logging

import httpx

from .config import Config
from .mensajes import MensajeEntrante

log = logging.getLogger("whatsapp.ia")

SYSTEM_PROMPT = (
    "Sos el asistente de ventas de VentasBot por WhatsApp. Respondé siempre "
    "en español de Paraguay, con tono amable, breve y directo (esto es un "
    "chat de WhatsApp, no un email formal). Si el cliente quiere hacer un "
    "pedido, pedile que confirme producto y cantidad, pero aclarale que un "
    "vendedor humano va a confirmar el pedido antes de despacharlo — "
    "todavía no podés crear pedidos vos mismo. No inventes precios, stock "
    "ni promesas de entrega que no te dieron explícitamente."
)

RESPUESTA_DE_RESPALDO = "Recibimos tu mensaje, en un momento te responde alguien del equipo."


async def generar_respuesta(cfg: Config, mensaje: MensajeEntrante) -> str:
    """Le pide al modelo local (Ollama) una respuesta para el mensaje entrante.

    Si Ollama no está disponible, tarda demasiado, o devuelve algo raro, cae
    a una respuesta de respaldo fija en vez de romper el flujo — Meta ya
    recibió el 200 en /webhook y no debe notarse un fallo interno acá.
    """
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{cfg.ollama_base_url}/v1/chat/completions",
                json={
                    "model": cfg.ollama_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": mensaje.texto},
                    ],
                    "stream": False,
                },
            )
            r.raise_for_status()
            data = r.json()
            texto = data["choices"][0]["message"]["content"].strip()
            return texto or RESPUESTA_DE_RESPALDO
    except Exception:
        log.exception("Ollama no respondió, uso la respuesta de respaldo")
        return RESPUESTA_DE_RESPALDO
