"""manual scan requests"""

from alembic import op
import sqlalchemy as sa


revision = "0005_manual_scan_requests"
down_revision = "0004_app_product_category"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "manual_scan_requests" not in inspector.get_table_names():
        op.create_table(
            "manual_scan_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("llm_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scan_run_id", sa.Integer(), sa.ForeignKey("scan_runs.id"), nullable=True),
            sa.Column("messages_found", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rule_candidates", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("scan_error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("classification_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("classification_processed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("accepted_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("excluded_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duplicate_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
        op.create_index("ix_manual_scan_requests_status", "manual_scan_requests", ["status"])
        op.create_index("ix_manual_scan_requests_scan_run_id", "manual_scan_requests", ["scan_run_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_scan_requests_one_active
        ON manual_scan_requests ((1))
        WHERE status IN ('queued', 'scanning', 'waiting_for_llm', 'classifying')
        """
    )


def downgrade():
    op.drop_index("uq_manual_scan_requests_one_active", table_name="manual_scan_requests")
    op.drop_index("ix_manual_scan_requests_scan_run_id", table_name="manual_scan_requests")
    op.drop_index("ix_manual_scan_requests_status", table_name="manual_scan_requests")
    op.drop_table("manual_scan_requests")
