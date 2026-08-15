"""Configuración leída de variables de entorno. Nada hardcodeado."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    verify_token: str
    app_secret: str
    phone_number_id: str
    access_token: str
    waba_id: str
    graph_version: str

    @property
    def url_mensajes(self) -> str:
        return (
            f"https://graph.facebook.com/{self.graph_version}"
            f"/{self.phone_number_id}/messages"
        )

    def faltantes(self) -> list[str]:
        """Variables imprescindibles que están vacías."""
        requeridas = {
            "VERIFY_TOKEN": self.verify_token,
            "APP_SECRET": self.app_secret,
            "PHONE_NUMBER_ID": self.phone_number_id,
            "ACCESS_TOKEN": self.access_token,
        }
        return [k for k, v in requeridas.items() if not v]


def cargar() -> Config:
    return Config(
        verify_token=os.getenv("VERIFY_TOKEN", ""),
        app_secret=os.getenv("APP_SECRET", ""),
        phone_number_id=os.getenv("PHONE_NUMBER_ID", ""),
        access_token=os.getenv("ACCESS_TOKEN", ""),
        waba_id=os.getenv("WABA_ID", ""),
        graph_version=os.getenv("GRAPH_VERSION", "v21.0"),
    )
