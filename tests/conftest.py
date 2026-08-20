"""Fija las variables de entorno ANTES de importar la app (config se lee al importar)."""

import os

os.environ.setdefault("VERIFY_TOKEN", "token-de-prueba")
os.environ.setdefault("APP_SECRET", "secreto-de-prueba")
os.environ.setdefault("PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("ACCESS_TOKEN", "EAA-falso")
os.environ.setdefault("GRAPH_VERSION", "v21.0")
os.environ.setdefault("JWT_SECRET", "secreto-de-pruebas-suficientemente-largo-para-hs256")
os.environ.setdefault("ENCRYPTION_KEY", "SDvJmpNI3rjbtwTVUeMKPPHf5MpRxAQg8AI2FrIILds=")
