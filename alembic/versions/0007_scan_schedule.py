"""editable automatic scan schedule"""

from alembic import op
import sqlalchemy as sa


revision = "0007_scan_schedule"
down_revision = "0006_manual_status_priority"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    manual_columns = {
        column["name"] for column in inspector.get_columns("manual_scan_requests")
    }
    if "trigger_source" not in manual_columns:
        op.add_column(
            "manual_scan_requests",
            sa.Column("trigger_source", sa.String(length=20), nullable=False, server_default="manual"),
        )

    if "scan_schedules" not in inspector.get_table_names():
        op.create_table(
            "scan_schedules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "scan_times",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("""'["04:00"]'::json"""),
            ),
            sa.Column("last_triggered_slot", sa.String(length=32), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.execute(
            """
            INSERT INTO scan_schedules (id, enabled, scan_times)
            VALUES (1, true, '["04:00"]'::json)
            ON CONFLICT (id) DO NOTHING
            """
        )


def downgrade():
    op.drop_table("scan_schedules")
    op.drop_column("manual_scan_requests", "trigger_source")
