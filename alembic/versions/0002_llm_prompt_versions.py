"""versioned LLM prompts and classification traceability"""

from alembic import op
import sqlalchemy as sa


revision = "0002_llm_prompt_versions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("llm_prompt_versions"):
        op.create_table(
            "llm_prompt_versions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("system_prompt", sa.Text(), nullable=False),
            sa.Column("change_note", sa.String(length=500), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("version"),
        )
        op.create_index("ix_llm_prompt_versions_is_active", "llm_prompt_versions", ["is_active"])

    message_columns = {column["name"] for column in inspector.get_columns("telegram_messages")}
    if "classification_model" not in message_columns:
        op.add_column("telegram_messages", sa.Column("classification_model", sa.String(length=255), nullable=True))
    if "classification_prompt_version" not in message_columns:
        op.add_column("telegram_messages", sa.Column("classification_prompt_version", sa.Integer(), nullable=True))
    if "classified_at" not in message_columns:
        op.add_column("telegram_messages", sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("telegram_messages", "classified_at")
    op.drop_column("telegram_messages", "classification_prompt_version")
    op.drop_column("telegram_messages", "classification_model")
    op.drop_index("ix_llm_prompt_versions_is_active", table_name="llm_prompt_versions")
    op.drop_table("llm_prompt_versions")
