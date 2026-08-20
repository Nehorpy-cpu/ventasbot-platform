"""Cifrado simétrico de los secretos que cargan las empresas.

El ACCESS_TOKEN de WhatsApp de un cliente da control total sobre su número:
mandar mensajes, leer plantillas, gastar su cupo. Guardarlo en claro en la
base significa que un dump de la base entrega las cuentas de WhatsApp de
todos los clientes. Por eso va cifrado con una clave que vive en el entorno,
no en la base.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_PLACEHOLDER = "cambiar-por-una-clave-fernet-generada"


def generar_clave() -> str:
    """Clave nueva lista para pegar en .env."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    clave = os.getenv("ENCRYPTION_KEY", "").strip()
    if not clave or clave == _PLACEHOLDER:
        raise RuntimeError(
            "ENCRYPTION_KEY sin configurar. Generá una con: "
            'python -c "from app.cripto import generar_clave; print(generar_clave())"'
        )
    try:
        return Fernet(clave.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("ENCRYPTION_KEY no es una clave Fernet válida (32 bytes en base64 urlsafe)") from exc


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(cifrado: str) -> str:
    """Devuelve el texto original.

    Si la clave cambió, esto explota a propósito: es preferible un error claro
    a mandarle a Meta un token corrupto y comerse un 401 sin saber por qué.
    """
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "No se pudo descifrar el token guardado: ¿cambió ENCRYPTION_KEY? "
            "Las empresas tienen que volver a cargar sus credenciales."
        ) from exc


def enmascarar(texto: str, visibles: int = 4) -> str:
    """Para mostrar en el panel sin revelar el secreto."""
    if not texto:
        return ""
    if len(texto) <= visibles:
        return "•" * len(texto)
    return "•" * 8 + texto[-visibles:]
