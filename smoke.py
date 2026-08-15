"""Prueba de humo contra el servidor ya levantado. Uso: python smoke.py [url]"""

import hashlib
import hmac
import json
import os
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
SECRETO = os.environ["APP_SECRET"]
VERIFY = os.environ["VERIFY_TOKEN"]

PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": "595981123456", "profile": {"name": "Ana"}}],
        "messages": [{"id": "wamid.1", "from": "595981123456", "timestamp": "1",
                      "type": "text", "text": {"body": "hola"}}]}}]}],
}

fallos = 0


def check(nombre, ok, detalle=""):
    global fallos
    print(("OK   " if ok else "FALLA") + f"  {nombre}  {detalle}")
    if not ok:
        fallos += 1


r = httpx.get(f"{BASE}/webhook", params={
    "hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"})
check("handshake devuelve el challenge crudo", r.status_code == 200 and r.text == "1158201444",
      f"[{r.status_code}] {r.text!r}")

r = httpx.get(f"{BASE}/webhook", params={
    "hub.mode": "subscribe", "hub.verify_token": "malo", "hub.challenge": "x"})
check("handshake con token malo -> 403", r.status_code == 403, f"[{r.status_code}]")

crudo = json.dumps(PAYLOAD).encode()
firma = "sha256=" + hmac.new(SECRETO.encode(), crudo, hashlib.sha256).hexdigest()

r = httpx.post(f"{BASE}/webhook", content=crudo, headers={
    "Content-Type": "application/json", "X-Hub-Signature-256": firma})
check("POST firmado -> 200", r.status_code == 200, f"[{r.status_code}]")

r = httpx.post(f"{BASE}/webhook", content=crudo, headers={
    "Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64})
check("POST con firma falsa -> 403", r.status_code == 403, f"[{r.status_code}]")

r = httpx.post(f"{BASE}/webhook", content=crudo, headers={"Content-Type": "application/json"})
check("POST sin firma -> 403", r.status_code == 403, f"[{r.status_code}]")

r = httpx.get(f"{BASE}/salud")
check("salud responde", r.status_code == 200, r.text)

print("\n", "TODO OK" if not fallos else f"{fallos} FALLAS")
sys.exit(1 if fallos else 0)
