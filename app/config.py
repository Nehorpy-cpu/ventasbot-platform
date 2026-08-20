"""Configuración leída de variables de entorno. Nada hardcodeado.

Ojo con qué es global y qué es por empresa:

- GLOBAL (acá): lo de la App de Meta de la plataforma — VERIFY_TOKEN y
  APP_SECRET. Una sola App recibe los webhooks de todos los clientes.
- POR EMPRESA (en la base, tabla whatsapp_accounts): el número y el token de
  cada cliente. Ver app/whatsapp.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# El .env ya lo cargó app/__init__.py.


@dataclass(frozen=True)
class Config:
    verify_token: str
    app_secret: str
    graph_version: str
    ollama_base_url: str
    ollama_model: str

    def faltantes(self) -> list[str]:
        """Variables imprescindibles que están vacías.

        PHONE_NUMBER_ID y ACCESS_TOKEN ya no están acá: los carga cada empresa
        desde su panel, no el .env de la plataforma.
        """
        requeridas = {
            "VERIFY_TOKEN": self.verify_token,
            "APP_SECRET": self.app_secret,
            "JWT_SECRET": os.getenv("JWT_SECRET", ""),
            "ENCRYPTION_KEY": os.getenv("ENCRYPTION_KEY", ""),
        }
        return [k for k, v in requeridas.items() if not v]


def cargar() -> Config:
    return Config(
        verify_token=os.getenv("VERIFY_TOKEN", ""),
        app_secret=os.getenv("APP_SECRET", ""),
        graph_version=os.getenv("GRAPH_VERSION", "v21.0"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
    )
