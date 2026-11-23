"web3.career website listings and language suitability"

from alembic import op
import sqlalchemy as sa


revision = "0009_web3_career_sources"
down_revision = "0008_message_dedup_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_job_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("apply_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending_classification"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("classification_model", sa.String(length=255), nullable=True),
        sa.Column("classification_prompt_version", sa.Integer(), nullable=True),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classification_result_json", sa.JSON(), nullable=True),
        sa.Column("dedup_group_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["dedup_group_id"], ["job_dedup_groups.id"]),
        sa.UniqueConstraint("source", "external_id", name="uq_web_job_listing_source_external"),
    )
    op.create_index("ix_web_job_listings_source", "web_job_listings", ["source"])
    op.create_index("ix_web_job_listings_status", "web_job_listings", ["status"])
    op.create_index("ix_web_job_listings_dedup_group_id", "web_job_listings", ["dedup_group_id"])
    op.alter_column("job_posts", "source_message_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("job_posts", sa.Column("source_web_listing_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_job_posts_source_web_listing", "job_posts", "web_job_listings", ["source_web_listing_id"], ["id"])
    op.create_unique_constraint("uq_job_posts_source_web_listing", "job_posts", ["source_web_listing_id"])
    op.add_column("job_posts", sa.Column("english_speaking_required", sa.Boolean(), nullable=True))
    op.add_column("job_posts", sa.Column("chinese_friendly_level", sa.String(length=40), nullable=True))
    op.add_column("job_posts", sa.Column("chinese_friendly_reason", sa.Text(), nullable=True))
    op.create_index("ix_job_posts_english_speaking_required", "job_posts", ["english_speaking_required"])
    op.create_index("ix_job_posts_chinese_friendly_level", "job_posts", ["chinese_friendly_level"])


def downgrade():
    op.drop_index("ix_job_posts_chinese_friendly_level", table_name="job_posts")
    op.drop_index("ix_job_posts_english_speaking_required", table_name="job_posts")
    op.drop_column("job_posts", "chinese_friendly_reason")
    op.drop_column("job_posts", "chinese_friendly_level")
    op.drop_column("job_posts", "english_speaking_required")
    op.drop_constraint("uq_job_posts_source_web_listing", "job_posts", type_="unique")
    op.drop_constraint("fk_job_posts_source_web_listing", "job_posts", type_="foreignkey")
    op.drop_column("job_posts", "source_web_listing_id")
    op.alter_column("job_posts", "source_message_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_web_job_listings_dedup_group_id", table_name="web_job_listings")
    op.drop_index("ix_web_job_listings_status", table_name="web_job_listings")
    op.drop_index("ix_web_job_listings_source", table_name="web_job_listings")
    op.drop_table("web_job_listings")
