"""Adaptadores de checkout. Nunca reciben secretos desde el navegador."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .models import Order, PaymentMethodConfig


@dataclass(frozen=True)
class CheckoutSession:
    external_id: str
    checkout_url: str


def _required_env(name: str, label: str) -> str:
    value = os.getenv(name, "") if name else ""
    if not value:
        raise RuntimeError(f"Falta configurar {label}")
    return value


def _validated_bancard_url(raw: str) -> str:
    parsed = urlparse(raw)
    allowed = {host.strip().lower() for host in os.getenv(
        "BANCARD_ALLOWED_HOSTS", "comercios.bancard.com.py"
    ).split(",") if host.strip()}
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowed:
        raise RuntimeError("api_url de Bancard no está en la lista de hosts HTTPS permitidos")
    if parsed.username or parsed.password or parsed.port not in (None, 443, 8888):
        raise RuntimeError("api_url de Bancard contiene credenciales o puerto no permitido")
    return raw.rstrip("/")


async def create_bancard_checkout(config: PaymentMethodConfig, order: Order) -> CheckoutSession:
    """Crea un link de pago con la API oficial Tpago de Bancard.

    La URL y los nombres de variables son por tenant. La firma se calcula sólo
    en servidor y las llaves jamás se incluyen en la respuesta.
    """
    public_key = _required_env(config.public_key_env, "llave pública Bancard")
    private_key = _required_env(config.private_key_env, "llave privada Bancard")
    if not config.api_url or not config.commerce_code or not config.branch_code:
        raise RuntimeError("Tpago requiere api_url, commerce_code y branch_code")
    credentials = base64.b64encode(f"{public_key};{private_key}".encode()).decode()
    payload = {
        "amount": order.total,
        "description": f"Pedido {order.id[-8:]}",
        "reference_id": order.id,
        "require_user_data": False,
    }
    api_url = _validated_bancard_url(config.api_url)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(
            f"{api_url}/external-commerce/api/0.1/commerces/{config.commerce_code}/"
            f"branches/{config.branch_code}/links/generate-payment-link",
            headers={"Authorization": f"Basic {credentials}"}, json=payload,
        )
        response.raise_for_status()
        data = response.json()
    link = data.get("payment_link") or {}
    if data.get("status") != "success" or not link.get("link_alias") or not link.get("link_url"):
        raise RuntimeError("Bancard no creó la sesión")
    external_id = str(link["link_alias"])
    checkout_url = str(link["link_url"])
    return CheckoutSession(external_id=external_id, checkout_url=checkout_url)
