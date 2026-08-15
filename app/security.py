"""Validación de la firma X-Hub-Signature-256 que manda Meta."""

from __future__ import annotations

import hashlib
import hmac


def firmar(cuerpo: bytes, app_secret: str) -> str:
    """Firma que Meta espera para este cuerpo. Útil también en los tests."""
    digest = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def firma_valida(cuerpo: bytes, cabecera: str | None, app_secret: str) -> bool:
    """Compara la firma recibida contra el HMAC de los BYTES CRUDOS del cuerpo.

    Calcularlo sobre el JSON ya parseado y re-serializado es el error clásico:
    cualquier diferencia de espacios o de orden de claves cambia el digest.
    """
    if not cabecera or not app_secret:
        return False
    return hmac.compare_digest(firmar(cuerpo, app_secret), cabecera)
