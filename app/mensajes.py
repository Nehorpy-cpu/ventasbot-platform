"""Parseo del payload entrante y envío de mensajes por la Cloud API.

El envío usa credenciales POR EMPRESA: cada tenant carga su propio número
(`phone_number_id`) y su propio `access_token`. La firma del webhook, en
cambio, sigue siendo global, porque una sola App de Meta recibe todo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Credenciales:
    """Lo mínimo para hablarle a Meta en nombre de una empresa."""

    phone_number_id: str
    access_token: str
    graph_version: str = "v21.0"

    @property
    def url_mensajes(self) -> str:
        return (
            f"https://graph.facebook.com/{self.graph_version}"
            f"/{self.phone_number_id}/messages"
        )


@dataclass(frozen=True)
class MensajeEntrante:
    id: str
    de: str          # numero internacional sin "+", ej. 595981123456
    tipo: str        # text, image, audio, interactive, ...
    texto: str       # vacio si el tipo no es text
    timestamp: str
    nombre: str      # nombre de perfil, si vino
    # A QUÉ número de la plataforma llegó. Es la clave para saber de qué
    # empresa es este mensaje: Meta lo manda en value.metadata.
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
            destino = (value.get("metadata") or {}).get("phone_number_id", "")

            nombres = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts") or []
            }

            for m in value.get("messages") or []:
                tipo = m.get("type", "")
                salida.append(
                    MensajeEntrante(
                        id=m.get("id", ""),
                        de=m.get("from", ""),
                        tipo=tipo,
                        texto=(m.get("text") or {}).get("body", "") if tipo == "text" else "",
                        timestamp=m.get("timestamp", ""),
                        nombre=nombres.get(m.get("from", ""), ""),
                        phone_number_id=destino,
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


async def enviar_texto(credenciales: Credenciales, para: str, texto: str) -> dict[str, Any]:
    """Envía un texto libre con las credenciales de UNA empresa.

    Solo funciona dentro de la ventana de 24 h desde el último mensaje del
    usuario. Fuera de esa ventana hay que usar una plantilla aprobada.
    """
    cuerpo = cuerpo_texto(para, texto)
    async with httpx.AsyncClient(timeout=15) as cliente:
        r = await cliente.post(
            credenciales.url_mensajes,
            headers={"Authorization": f"Bearer {credenciales.access_token}"},
            json=cuerpo,
        )
        r.raise_for_status()
        return r.json()


async def probar_credenciales(credenciales: Credenciales) -> tuple[bool, str]:
    """Consulta el número en Graph para saber si el token sirve de verdad.

    Devuelve (ok, detalle). Se usa cuando una empresa carga sus datos: es
    preferible que se entere en el panel y no cuando un cliente escribe.
    """
    url = (
        f"https://graph.facebook.com/{credenciales.graph_version}"
        f"/{credenciales.phone_number_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as cliente:
            r = await cliente.get(
                url,
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {credenciales.access_token}"},
            )
    except httpx.HTTPError as exc:
        return False, f"No se pudo contactar a Meta: {exc.__class__.__name__}"
    if r.status_code == 200:
        datos = r.json()
        return True, datos.get("display_phone_number", "")
    detalle = (r.json().get("error", {}) or {}).get("message", r.text[:200]) if r.content else r.reason_phrase
    return False, detalle
