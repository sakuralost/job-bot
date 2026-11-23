"""dashboard-managed website sources"""

from alembic import op
import sqlalchemy as sa


revision = "0011_web_source_management"
down_revision = "0010_web_job_listing_raw_payload"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_web_sources_source_key"),
    )
    op.create_index("ix_web_sources_source_key", "web_sources", ["source_key"])


def downgrade():
    op.drop_index("ix_web_sources_source_key", table_name="web_sources")
    op.drop_table("web_sources")
