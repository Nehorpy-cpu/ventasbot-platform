"""Generación de respuestas con el modelo de IA local (Ollama).

Ahora que cada empresa carga su propio número, el webhook sabe de qué tenant
es cada mensaje entrante. Eso permite armar el prompt con el catálogo real de
esa empresa: precios, stock y nombres de sus productos, y no una persona
genérica que no sabe qué vende.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from .config import Config
from .mensajes import MensajeEntrante

log = logging.getLogger("whatsapp.ia")

MAX_PRODUCTOS_EN_PROMPT = 40


@dataclass(frozen=True)
class ContextoTenant:
    """Lo que el modelo necesita saber de la empresa para atender bien."""

    nombre_empresa: str
    moneda: str = "PYG"
    productos: list[tuple[str, int, int]] = field(default_factory=list)  # (nombre, precio, stock)

    def catalogo_en_texto(self) -> str:
        if not self.productos:
            return "El catálogo todavía no está cargado."
        lineas = []
        for nombre, precio, stock in self.productos[:MAX_PRODUCTOS_EN_PROMPT]:
            disponible = f"{stock} disponibles" if stock > 0 else "SIN STOCK"
            lineas.append(f"- {nombre}: {precio:,} {self.moneda} ({disponible})".replace(",", "."))
        if len(self.productos) > MAX_PRODUCTOS_EN_PROMPT:
            lineas.append(f"- (y {len(self.productos) - MAX_PRODUCTOS_EN_PROMPT} productos más)")
        return "\n".join(lineas)


BASE_PROMPT = (
    "Sos el asistente de ventas de {empresa} por WhatsApp. Respondé siempre "
    "en español de Paraguay, con tono amable, breve y directo (esto es un "
    "chat de WhatsApp, no un email formal).\n\n"
    "Catálogo actual:\n{catalogo}\n\n"
    "Reglas que no podés romper:\n"
    "- No inventes precios, stock ni productos: si algo no está en el catálogo "
    "de arriba, decí que no lo tenés y ofrecé lo que sí hay.\n"
    "- Si un producto figura SIN STOCK, no lo ofrezcas como disponible.\n"
    "- Si el cliente quiere comprar, pedile que confirme producto y cantidad, y "
    "avisale que un vendedor del local confirma el pedido antes de despacharlo.\n"
    "- No prometas plazos de entrega que no te dieron explícitamente."
)

PROMPT_SIN_EMPRESA = (
    "Sos un asistente de ventas por WhatsApp. Respondé en español de Paraguay, "
    "breve y amable. No inventes precios, stock ni promesas de entrega."
)

RESPUESTA_DE_RESPALDO = "Recibimos tu mensaje, en un momento te responde alguien del equipo."


def armar_prompt(contexto: ContextoTenant | None) -> str:
    if contexto is None:
        return PROMPT_SIN_EMPRESA
    return BASE_PROMPT.format(
        empresa=contexto.nombre_empresa,
        catalogo=contexto.catalogo_en_texto(),
    )


async def generar_respuesta(
    cfg: Config,
    mensaje: MensajeEntrante,
    contexto: ContextoTenant | None = None,
) -> str:
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
                        {"role": "system", "content": armar_prompt(contexto)},
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
