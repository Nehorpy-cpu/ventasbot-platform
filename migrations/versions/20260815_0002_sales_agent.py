"""Capa comercial: playbooks, calificación y aprobaciones.

Revision ID: 20260815_0002
Revises: 20260815_0001
"""

from alembic import op
import sqlalchemy as sa

revision = "20260815_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    customer_columns = _columns("customers")
    for column in (
        sa.Column("lead_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lead_temperature", sa.String(20), nullable=False, server_default="COLD"),
        sa.Column("purchase_intent", sa.String(80), nullable=False, server_default="UNKNOWN"),
        sa.Column("estimated_budget", sa.Integer(), nullable=True),
        sa.Column("urgency", sa.String(20), nullable=True),
        sa.Column("next_action", sa.String(200), nullable=True),
    ):
        if column.name not in customer_columns:
            op.add_column("customers", column)

    tables = _tables()
    if "sales_playbooks" not in tables:
        op.create_table(
            "sales_playbooks",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("mode", sa.Enum("DRAFT", "AUTOMATIC", name="agentmode"), nullable=False),
            sa.Column("brand_tone", sa.Text(), nullable=False),
            sa.Column("hot_threshold", sa.Integer(), nullable=False, server_default="70"),
            sa.Column("warm_threshold", sa.Integer(), nullable=False, server_default="40"),
            sa.Column("auto_send_min_confidence", sa.Integer(), nullable=False, server_default="90"),
            sa.Column("escalation_words", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_sales_playbooks_tenant_id", "sales_playbooks", ["tenant_id"], unique=True)
    if "sales_objections" not in tables:
        op.create_table(
            "sales_objections",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("triggers", sa.Text(), nullable=False),
            sa.Column("response", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "name", name="uq_sales_objection_tenant_name"),
        )
        op.create_index("ix_sales_objections_tenant_id", "sales_objections", ["tenant_id"])
    if "agent_runs" not in tables:
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversations.id"), nullable=False),
            sa.Column("customer_id", sa.String(32), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("input_text", sa.Text(), nullable=False),
            sa.Column("intent", sa.String(80), nullable=False),
            sa.Column("confidence", sa.Integer(), nullable=False),
            sa.Column("lead_score", sa.Integer(), nullable=False),
            sa.Column("temperature", sa.String(20), nullable=False),
            sa.Column("objection_name", sa.String(100), nullable=True),
            sa.Column("suggested_reply", sa.Text(), nullable=False),
            sa.Column("decision", sa.String(80), nullable=False),
            sa.Column("status", sa.Enum("COMPLETED", "PENDING_APPROVAL", "ESCALATED", "FAILED", name="agentrunstatus"), nullable=False),
            sa.Column("steps_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
        op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
        op.create_index("ix_agent_runs_customer_id", "agent_runs", ["customer_id"])
        op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])
    if "pending_agent_actions" not in tables:
        op.create_table(
            "pending_agent_actions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("run_id", sa.String(32), sa.ForeignKey("agent_runs.id"), nullable=False, unique=True),
            sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversations.id"), nullable=False),
            sa.Column("customer_id", sa.String(32), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("action_type", sa.String(40), nullable=False),
            sa.Column("proposed_text", sa.Text(), nullable=False),
            sa.Column("status", sa.Enum("PENDING", "APPROVED", "REJECTED", name="pendingactionstatus"), nullable=False),
            sa.Column("resolved_by_id", sa.String(32), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_pending_agent_actions_tenant_id", "pending_agent_actions", ["tenant_id"])
        op.create_index("ix_pending_agent_actions_run_id", "pending_agent_actions", ["run_id"], unique=True)
        op.create_index("ix_pending_agent_actions_conversation_id", "pending_agent_actions", ["conversation_id"])
        op.create_index("ix_pending_agent_actions_customer_id", "pending_agent_actions", ["customer_id"])
        op.create_index("ix_pending_agent_actions_created_at", "pending_agent_actions", ["created_at"])


def downgrade() -> None:
    tables = _tables()
    for table in ("pending_agent_actions", "agent_runs", "sales_objections", "sales_playbooks"):
        if table in tables:
            op.drop_table(table)
    customer_columns = _columns("customers")
    for name in ("next_action", "urgency", "estimated_budget", "purchase_intent", "lead_temperature", "lead_score"):
        if name in customer_columns:
            op.drop_column("customers", name)

