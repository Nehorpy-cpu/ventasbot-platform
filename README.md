# VentasBot — plataforma de ventas por WhatsApp

Multiempresa: una sola instalación atiende a varios negocios, y **cada empresa
carga su propio número de WhatsApp** desde su panel. FastAPI + SQLAlchemy +
Cloud API oficial de Meta. Sin librerías no oficiales de WhatsApp.

## Arrancar en local

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
```

Completar `.env` (ver más abajo qué es obligatorio), crear el esquema y el
superadmin:

```bash
.venv\Scripts\python.exe -m alembic upgrade head
```

```bash
.venv\Scripts\python.exe -m app.seed
```

Levantar:

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8099
```

- Panel: <http://127.0.0.1:8099/panel/>
- Chequeo de configuración: `GET /salud` — dice qué variables faltan y si la
  base responde.

## Cómo funciona el multiempresa

Esta es la decisión que ordena todo lo demás:

- **Una sola App de Meta**, la de la plataforma, recibe TODOS los webhooks. Por
  eso `VERIFY_TOKEN` y `APP_SECRET` son globales y viven en `.env`.
- **Cada empresa carga su propio número**: `phone_number_id` + `access_token`,
  desde el panel (menú **WhatsApp**). Se guardan en `whatsapp_accounts`, con el
  token **cifrado**.
- Meta manda `value.metadata.phone_number_id` en cada payload: ese es el número
  al que llegó el mensaje, y con él se resuelve la empresa dueña.
- Si ningún tenant reclama ese número, **no se contesta**. Responder con las
  credenciales de otra empresa sería peor que el silencio.
- `phone_number_id` es único en toda la plataforma: dos empresas no pueden
  reclamar el mismo número.

## Variables de entorno

Obligatorias (la app no arranca o rechaza todo sin ellas):

| Variable | Para qué |
|---|---|
| `VERIFY_TOKEN` | Handshake del webhook. Lo inventás vos y lo pegás igual en Meta. |
| `APP_SECRET` | Valida la firma de Meta. Sin esto **todo webhook se rechaza con 403**. |
| `JWT_SECRET` | Firma los tokens del panel. Mínimo 32 caracteres, distinto del placeholder. |
| `ENCRYPTION_KEY` | Cifra los tokens de WhatsApp de cada empresa. Clave Fernet. |

Opcionales: `DATABASE_URL`, `GRAPH_VERSION`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`,
`JWT_EXPIRE_MINUTES`, `PUBLIC_BASE_URL` (sin esta, los avisos salen sin link de
seguimiento).

`PHONE_NUMBER_ID` y `ACCESS_TOKEN` **ya no se usan**: los carga cada empresa.

## Rutas

| Ruta | Qué hace |
|---|---|
| `GET /webhook` | Handshake de Meta: devuelve `hub.challenge` en texto plano. |
| `POST /webhook` | Valida firma, deduplica, resuelve la empresa y responde 200 al toque. |
| `GET /salud` | Variables faltantes y estado de la base. |
| `/panel/` | Panel: catálogo, pedidos, cobros, delivery y carga del número. |
| `GET /seguimiento/{token}` | Página pública que se le manda al cliente. |
| `/api/...` | API del panel. Documentación viva en `/docs`. |

## Pruebas

```bash
.venv\Scripts\python.exe -m pytest -q
```

70 tests: handshake, firma, parseo, ruteo multiempresa, aislamiento entre
empresas, stock, pagos, delivery, avisos y las regresiones de
`tests/test_regresiones.py`.

Antes de cerrar cualquier cambio (lo corre también el CI):

```bash
.venv\Scripts\python.exe -m bandit -r app -q --severity-level medium
```

```bash
.venv\Scripts\python.exe -m pip_audit
```

## Migraciones

El esquema lo maneja Alembic. La app **se niega a arrancar** si la base no está
migrada, en vez de crear tablas al vuelo y tapar el problema.

```bash
.venv\Scripts\python.exe -m alembic upgrade head
```

Después de tocar `app/models.py`:

```bash
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "que cambio"
```

`alembic check` falla si los modelos y las migraciones se desincronizaron.

## Detalles que rompen si se hacen mal

- El challenge se devuelve **crudo en texto plano**. Como JSON queda entre
  comillas y Meta rechaza la verificación.
- Los parámetros llegan como `hub.mode` / `hub.verify_token` / `hub.challenge`,
  con punto: en FastAPI hay que declararlos con `Query(alias=...)` o da 422.
- La firma se calcula sobre los **bytes crudos** del cuerpo, nunca sobre el JSON
  re-serializado (hay un test que lo demuestra).
- El mismo webhook recibe acuses de entrega en `statuses`. Si no se filtran, cada
  mensaje propio vuelve como entrante y el bot se responde a sí mismo.
- Se responde 200 rápido y el trabajo va a background: Meta reintenta si tardás.
- Los reintentos repiten el mismo `message.id`. El webhook lleva un registro
  acotado de ids ya atendidos (`ya_procesado` en `app/main.py`).
- `enviar_texto` solo sirve dentro de la ventana de 24 h. Fuera de ella, plantilla
  aprobada — los avisos de estado fallan con un error registrado en el log.

## Invariantes del negocio (no romper sin test)

- El stock se descuenta al pasar a `CONFIRMED` y se repone si el pedido se
  cancela desde cualquier estado posterior. Las filas se leen con
  `with_for_update()`: en PostgreSQL eso evita vender dos veces la misma unidad.
- La suma de pagos `PENDING` + `APPROVED` nunca puede superar el total del
  pedido. Se confirma solo cuando lo **aprobado** cubre el total.
- El token del panel trae rol y empresa, pero manda la base: si a alguien lo
  cambian de rol o de empresa, su token anterior deja de valer.
- Los `access_token` de WhatsApp nunca vuelven por la API: solo enmascarados.
  Tampoco aparecen en los mensajes de error de Meta.

## Lo que vive en memoria del proceso (mudar a Redis si hay varios workers)

- El registro anti-duplicados de `message.id`.
- El contador de intentos fallidos de login.

Con un solo worker funcionan. Con varios, cada uno tendría su propia copia.

## Desplegar

Necesita HTTPS público (Cloudflare Tunnel, Railway, Fly…). En la consola de
Meta: cargar la URL y el `VERIFY_TOKEN`, **suscribirse al campo `messages`**, y
publicar la app. Después, cada empresa carga su número desde su panel. El
procedimiento del lado de Meta está en el skill `whatsapp-cloud-api`.
