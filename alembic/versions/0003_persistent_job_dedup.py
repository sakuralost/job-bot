"""persistent job deduplication and resumable reclassification"""

from alembic import op
import sqlalchemy as sa


revision = "0003_persistent_job_dedup"
down_revision = "0002_llm_prompt_versions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("job_dedup_groups"):
        op.create_table(
            "job_dedup_groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), server_default="new", nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("occurrence_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_job_dedup_groups_status", "job_dedup_groups", ["status"])

    if not inspector.has_table("job_dedup_keys"):
        op.create_table(
            "job_dedup_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("key_type", sa.String(length=40), nullable=False),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(["group_id"], ["job_dedup_groups.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key_type", "key_hash", name="uq_job_dedup_key"),
        )
        op.create_index("ix_job_dedup_keys_group_id", "job_dedup_keys", ["group_id"])

    message_columns = {column["name"] for column in inspector.get_columns("telegram_messages")}
    if "dedup_group_id" not in message_columns:
        op.add_column("telegram_messages", sa.Column("dedup_group_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_telegram_messages_dedup_group",
            "telegram_messages",
            "job_dedup_groups",
            ["dedup_group_id"],
            ["id"],
        )
        op.create_index("ix_telegram_messages_dedup_group_id", "telegram_messages", ["dedup_group_id"])

    job_columns = {column["name"] for column in inspector.get_columns("job_posts")}
    if "dedup_group_id" not in job_columns:
        op.add_column("job_posts", sa.Column("dedup_group_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_job_posts_dedup_group",
            "job_posts",
            "job_dedup_groups",
            ["dedup_group_id"],
            ["id"],
        )
        op.create_index("ix_job_posts_dedup_group_id", "job_posts", ["dedup_group_id"])
    if "duplicate_of_job_post_id" not in job_columns:
        op.add_column("job_posts", sa.Column("duplicate_of_job_post_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_job_posts_duplicate_of",
            "job_posts",
            "job_posts",
            ["duplicate_of_job_post_id"],
            ["id"],
        )
        op.create_index("ix_job_posts_duplicate_of_job_post_id", "job_posts", ["duplicate_of_job_post_id"])

    if not inspector.has_table("reclassification_runs"):
        op.create_table(
            "reclassification_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("prompt_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
            sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("processed_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("accepted_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("excluded_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("duplicate_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("error_items", sa.Integer(), server_default="0", nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_reclassification_runs_prompt_version", "reclassification_runs", ["prompt_version"])
        op.create_index("ix_reclassification_runs_status", "reclassification_runs", ["status"])

    if not inspector.has_table("reclassification_items"):
        op.create_table(
            "reclassification_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=False),
            sa.Column("job_post_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
            sa.Column("outcome", sa.String(length=40), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["job_post_id"], ["job_posts.id"]),
            sa.ForeignKeyConstraint(["message_id"], ["telegram_messages.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["reclassification_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "message_id", name="uq_reclassification_run_message"),
        )
        op.create_index("ix_reclassification_items_run_id", "reclassification_items", ["run_id"])
        op.create_index("ix_reclassification_items_message_id", "reclassification_items", ["message_id"])
        op.create_index("ix_reclassification_items_job_post_id", "reclassification_items", ["job_post_id"])
        op.create_index("ix_reclassification_items_status", "reclassification_items", ["status"])


def downgrade():
    op.drop_table("reclassification_items")
    op.drop_table("reclassification_runs")
    op.drop_index("ix_job_posts_duplicate_of_job_post_id", table_name="job_posts")
    op.drop_constraint("fk_job_posts_duplicate_of", "job_posts", type_="foreignkey")
    op.drop_column("job_posts", "duplicate_of_job_post_id")
    op.drop_index("ix_job_posts_dedup_group_id", table_name="job_posts")
    op.drop_constraint("fk_job_posts_dedup_group", "job_posts", type_="foreignkey")
    op.drop_column("job_posts", "dedup_group_id")
    op.drop_index("ix_telegram_messages_dedup_group_id", table_name="telegram_messages")
    op.drop_constraint("fk_telegram_messages_dedup_group", "telegram_messages", type_="foreignkey")
    op.drop_column("telegram_messages", "dedup_group_id")
    op.drop_table("job_dedup_keys")
    op.drop_table("job_dedup_groups")
