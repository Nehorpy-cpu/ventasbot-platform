"""Parseo del payload entrante y envío de mensajes por la Cloud API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config


@dataclass(frozen=True)
class MensajeEntrante:
    id: str
    de: str          # numero internacional sin "+", ej. 595981123456
    tipo: str        # text, image, audio, interactive, ...
    texto: str       # vacio si el tipo no es text
    timestamp: str
    nombre: str      # nombre de perfil, si vino
    datos: dict[str, Any]
    phone_number_id: str = ""


def extraer_mensajes(payload: dict[str, Any]) -> list[MensajeEntrante]:
    """Saca los mensajes de usuario del payload de Meta.

    El mismo webhook recibe acuses de entrega en `statuses`. Si no se filtran,
    cada mensaje propio vuelve como si fuera entrante y el bot se responde solo.
    Acá simplemente se ignoran: solo se leen las entradas con `messages`.
    """
    salida: list[MensajeEntrante] = []

    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")

            nombres = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts") or []
            }

            for m in value.get("messages") or []:
                tipo = m.get("type", "")
                datos = m.get(tipo) or {}
                texto = ""
                if tipo == "text":
                    texto = datos.get("body", "")
                elif tipo == "interactive":
                    seleccion = datos.get("button_reply") or datos.get("list_reply") or {}
                    texto = seleccion.get("title") or seleccion.get("id", "")
                elif tipo == "button":
                    texto = datos.get("text", "")
                salida.append(
                    MensajeEntrante(
                        id=m.get("id", ""),
                        de=m.get("from", ""),
                        tipo=tipo,
                        texto=texto,
                        timestamp=m.get("timestamp", ""),
                        nombre=nombres.get(m.get("from", ""), ""),
                        datos=datos,
                        phone_number_id=phone_number_id,
                    )
                )

    return salida


def cuerpo_texto(para: str, texto: str) -> dict[str, Any]:
    """Payload de un mensaje de texto. El `to` va internacional, sin `+` ni espacios."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": para.lstrip("+").replace(" ", "").replace("-", ""),
        "type": "text",
        "text": {"preview_url": False, "body": texto},
    }


async def enviar_texto(cfg: Config, para: str, texto: str) -> dict[str, Any]:
    """Envía un texto libre.

    Solo funciona dentro de la ventana de 24 h desde el último mensaje del
    usuario. Fuera de esa ventana hay que usar una plantilla aprobada.
    """
    cuerpo = cuerpo_texto(para, texto)
    async with httpx.AsyncClient(timeout=15) as cliente:
        r = await cliente.post(
            cfg.url_mensajes,
            headers={"Authorization": f"Bearer {cfg.access_token}"},
            json=cuerpo,
        )
        r.raise_for_status()
        return r.json()
