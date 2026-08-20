"""VentasBot.

El .env se carga acá, en el paquete, y no en cada módulo: así cualquier punto
de entrada (uvicorn, `python -m app.seed`, alembic) ve las mismas variables.
Antes solo lo cargaba app/config.py, y por eso `app.seed` no encontraba
SUPERADMIN_PASSWORD aunque estuviera en el archivo.
"""

from dotenv import load_dotenv

load_dotenv()
