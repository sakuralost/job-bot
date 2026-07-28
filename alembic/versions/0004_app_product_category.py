"""APP product manager category"""

from alembic import op
import sqlalchemy as sa


revision = "0004_app_product_category"
down_revision = "0003_persistent_job_dedup"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("job_posts")}
    if "is_app_product_role" not in columns:
        op.add_column(
            "job_posts",
            sa.Column("is_app_product_role", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.create_index("ix_job_posts_is_app_product_role", "job_posts", ["is_app_product_role"])


def downgrade():
    op.drop_index("ix_job_posts_is_app_product_role", table_name="job_posts")
    op.drop_column("job_posts", "is_app_product_role")
