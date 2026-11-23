"""retain raw website job payloads for the generic external-job store"""

from alembic import op
import sqlalchemy as sa


revision = "0010_web_job_listing_raw_payload"
down_revision = "0009_web3_career_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("web_job_listings", sa.Column("raw_payload", sa.JSON(), nullable=True))
    op.create_index("ix_web_job_listings_source_published_at", "web_job_listings", ["source", "published_at"])
    op.create_index("ix_web_job_listings_company_name", "web_job_listings", ["company_name"])


def downgrade():
    op.drop_index("ix_web_job_listings_company_name", table_name="web_job_listings")
    op.drop_index("ix_web_job_listings_source_published_at", table_name="web_job_listings")
    op.drop_column("web_job_listings", "raw_payload")
