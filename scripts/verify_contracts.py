"""Verificador sin dependencias para invariantes estructurales del repositorio."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    python_files = sorted((*ROOT.glob("app/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("migrations/**/*.py")))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    models = (ROOT / "app/models.py").read_text(encoding="utf-8")
    api = (ROOT / "app/api.py").read_text(encoding="utf-8")
    crm = (ROOT / "app/crm.py").read_text(encoding="utf-8")
    payment = (ROOT / "app/payment_gateway.py").read_text(encoding="utf-8")
    webhook = (ROOT / "app/payment_webhook.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    tenant_models = ["Product", "Customer", "WhatsappIntegration", "Conversation", "ConversationMessage",
                     "Order", "Payment", "PaymentMethodConfig", "Invoice", "Delivery", "DeliveryEvent"]
    for index, name in enumerate(tenant_models):
        start = models.index(f"class {name}(")
        later = [models.find(f"class {other}(", start + 1) for other in tenant_models[index + 1:]]
        end = min((position for position in later if position >= 0), default=len(models))
        require("tenant_id:" in models[start:end], f"{name} debe ser tenant-scoped")

    require("UniqueConstraint(\"tenant_id\", \"idempotency_key\"" in models,
            "Idempotencia de pago debe estar aislada por tenant")
    require("tracking_expires_at" in models and "status_code=410" in api,
            "Tracking público debe expirar")
    require("with_for_update()" in api and "with_for_update()" in (ROOT / "app/services.py").read_text(),
            "Catálogo/stock requieren bloqueo transaccional")
    require("_tenant_secret_prefix" in api and "_validated_bancard_url" in payment,
            "Secretos y salida Bancard deben estar acotados")
    require("incoming.phone_number_id" in crm, "Cada mensaje Meta debe rutearse por su propio número")
    require("BANCARD_CALLBACK_IPS" in webhook and "amount != payment.amount" in webhook,
            "Callback Bancard requiere origen y monto verificados")
    require(re.search(r"^JWT_SECRET=\s*$", env_example, re.MULTILINE) is not None,
            "El ejemplo no debe contener un secreto JWT conocido")
    require("alembic upgrade head" in (ROOT / "Dockerfile").read_text(encoding="utf-8"),
            "Producción debe aplicar migraciones antes de arrancar")
    print(f"OK: {len(python_files)} archivos Python y 9 invariantes estructurales verificados")


if __name__ == "__main__":
    main()
