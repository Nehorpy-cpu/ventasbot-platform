"""Esquema inicial completo de VentasBot.

Revision ID: 20260815_0001
"""

from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision = "20260815_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Primera instalación desde cero: la metadata es el contrato del esquema v1.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Sólo para entornos desechables; producción restaura backup y avanza con otra revisión.
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
