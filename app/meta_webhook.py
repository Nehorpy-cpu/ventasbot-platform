"""Webhook oficial de Meta que enruta cada número a su tenant."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import PlainTextResponse

from .config import cargar
from .crm import process_meta_payload
from .security import firma_valida

router = APIRouter(prefix="/webhooks/meta", tags=["WhatsApp"])


@router.get("", response_class=PlainTextResponse)
def verify(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    cfg = cargar()
    if mode == "subscribe" and token and token == cfg.verify_token:
        return PlainTextResponse(challenge or "")
    return Response(status_code=403)


@router.post("")
async def receive(request: Request, tasks: BackgroundTasks) -> Response:
    raw = await request.body()
    cfg = cargar()
    if not firma_valida(raw, request.headers.get("X-Hub-Signature-256"), cfg.app_secret):
        return Response(status_code=403)
    payload = await request.json()
    tasks.add_task(process_meta_payload, payload)
    return Response(status_code=200)
