import asyncio

import httpx

from app.config import Config
from app.ia import RESPUESTA_DE_RESPALDO, generar_respuesta
from app.mensajes import MensajeEntrante


def _cfg() -> Config:
    return Config(
        verify_token="v", app_secret="s", graph_version="v21.0",
        ollama_base_url="http://localhost:11434", ollama_model="qwen3:8b",
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


# --- el prompt lleva el catálogo de la empresa -----------------------------

def test_el_prompt_incluye_los_productos_de_la_empresa():
    from app.ia import ContextoTenant, armar_prompt

    prompt = armar_prompt(ContextoTenant(
        nombre_empresa="Pizzería Central",
        productos=[("Pizza muzzarella", 35000, 12), ("Empanada", 5000, 0)],
    ))
    assert "Pizzería Central" in prompt
    assert "Pizza muzzarella" in prompt
    assert "35.000 PYG" in prompt
    assert "12 disponibles" in prompt
    assert "SIN STOCK" in prompt          # la empanada no se ofrece como disponible
    assert "No inventes precios" in prompt


def test_sin_catalogo_cargado_el_prompt_lo_dice():
    from app.ia import ContextoTenant, armar_prompt

    prompt = armar_prompt(ContextoTenant(nombre_empresa="Empresa Nueva"))
    assert "catálogo todavía no está cargado" in prompt


def test_el_prompt_no_manda_un_catalogo_infinito():
    from app.ia import MAX_PRODUCTOS_EN_PROMPT, ContextoTenant

    contexto = ContextoTenant(
        nombre_empresa="Mayorista",
        productos=[(f"Producto {i}", 1000, 5) for i in range(MAX_PRODUCTOS_EN_PROMPT + 25)],
    )
    texto = contexto.catalogo_en_texto()
    assert texto.count("\n") <= MAX_PRODUCTOS_EN_PROMPT + 1
    assert "25 productos más" in texto
