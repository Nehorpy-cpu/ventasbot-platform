"""Fija las variables de entorno ANTES de importar la app (config se lee al importar)."""

import os

os.environ.setdefault("VERIFY_TOKEN", "token-de-prueba")
os.environ.setdefault("APP_SECRET", "secreto-de-prueba")
os.environ.setdefault("PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("ACCESS_TOKEN", "EAA-falso")
os.environ.setdefault("GRAPH_VERSION", "v21.0")
os.environ.setdefault("JWT_SECRET", "tests-only-secret-with-more-than-32-characters")
os.environ.setdefault("BANCARD_CALLBACK_IPS", "testclient")
