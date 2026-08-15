# Webhook de WhatsApp Cloud API

Endpoint para recibir y responder mensajes de WhatsApp por la **Cloud API oficial de
Meta**. FastAPI + httpx. Sin librerías no oficiales.

## Arrancar en local

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
```

Completar `.env` y levantar:

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8099
```

Chequeo rápido de configuración: `GET http://127.0.0.1:8099/salud` — lista qué
variables faltan.

## Pruebas

```bash
.venv\Scripts\python.exe -m pytest -q
```

17 tests: handshake, validación de firma, parseo del payload y normalización del
número. Para probar contra un servidor levantado de verdad:

```bash
.venv\Scripts\python.exe smoke.py http://127.0.0.1:8099
```

## Qué hace

| Ruta | Qué hace |
|---|---|
| `GET /webhook` | Handshake de Meta: devuelve `hub.challenge` en texto plano si el `hub.verify_token` coincide. |
| `POST /webhook` | Valida `X-Hub-Signature-256`, encola el procesamiento y responde 200 al toque. |
| `GET /salud` | Reporta qué variables de entorno faltan. |

La lógica del bot está en `procesar()` en [app/main.py](app/main.py) — hoy es un eco.
Ahí se enchufa la IA o lo que corresponda.

## Detalles que rompen si se hacen mal

- El challenge se devuelve **crudo en texto plano**. Como JSON queda entre comillas y
  Meta rechaza la verificación.
- Los parámetros llegan como `hub.mode` / `hub.verify_token` / `hub.challenge`, con
  punto: en FastAPI hay que declararlos con `Query(alias=...)` o da 422.
- La firma se calcula sobre los **bytes crudos** del cuerpo, nunca sobre el JSON
  re-serializado (hay un test que lo demuestra).
- El mismo webhook recibe acuses de entrega en `statuses`. Si no se filtran, cada
  mensaje propio vuelve como entrante y el bot se responde a sí mismo.
- Se responde 200 rápido y el trabajo va a background: Meta reintenta si tardás, y los
  reintentos duplican mensajes.
- `enviar_texto` solo sirve dentro de la ventana de 24 h. Fuera de ella, plantilla
  aprobada.

## Desplegar

Necesita HTTPS público (Cloudflare Tunnel/Workers, Railway, Vercel…). Después, en la
consola de Meta: cargar la URL y el `VERIFY_TOKEN`, **suscribirse al campo `messages`**,
publicar la app, y reemplazar el token temporal por uno permanente de usuario del
sistema. El procedimiento completo está en el skill `whatsapp-cloud-api`.
