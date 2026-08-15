"""Inicialización común del paquete de VentasBot."""

from dotenv import load_dotenv

# Todos los puntos de entrada (API, seed, migraciones y scripts) deben recibir
# la misma configuración local. Las variables ya definidas por el entorno de
# producción conservan prioridad porque load_dotenv no las sobrescribe.
load_dotenv()
