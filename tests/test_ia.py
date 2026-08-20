import asyncio

import httpx

from app.config import Config
from app.ia import RESPUESTA_DE_RESPALDO, generar_respuesta
from app.mensajes import MensajeEntrante


def _cfg() -> Config:
    return Config(
        verify_token="v", app_secret="s", phone_number_id="p", access_token="a",
        waba_id="w", graph_version="v21.0",
        ollama_base_url="http://localhost:11434", ollama_model="qwen3.8:27b",
    )


def _mensaje(texto: str) -> MensajeEntrante:
    return MensajeEntrante(id="1", de="595981123456", tipo="text", texto=texto,
                            timestamp="1", nombre="Ana")


class _RespuestaFalsa:
    def __init__(self, cuerpo: dict):
        self._cuerpo = cuerpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._cuerpo


class _ClienteFalso:
    def __init__(self, respuesta=None, excepcion=None):
        self._respuesta = respuesta
        self._excepcion = excepcion

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, *_a, **_kw):
        if self._excepcion:
            raise self._excepcion
        return self._respuesta


def test_generar_respuesta_devuelve_el_texto_del_modelo(monkeypatch):
    cuerpo = {"choices": [{"message": {"content": "  Hola! En qué te puedo ayudar?  "}}]}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _ClienteFalso(respuesta=_RespuestaFalsa(cuerpo)))
    resultado = asyncio.run(generar_respuesta(_cfg(), _mensaje("hola")))
    assert resultado == "Hola! En qué te puedo ayudar?"


def test_generar_respuesta_cae_al_respaldo_si_ollama_falla(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _ClienteFalso(excepcion=httpx.ConnectError("no anda")))
    resultado = asyncio.run(generar_respuesta(_cfg(), _mensaje("hola")))
    assert resultado == RESPUESTA_DE_RESPALDO
