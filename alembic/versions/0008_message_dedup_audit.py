"""pre-LLM message deduplication and classification audit"""

from alembic import op
import sqlalchemy as sa


revision = "0008_message_dedup_audit"
down_revision = "0007_scan_schedule"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("telegram_messages")}
    if "content_fingerprint" not in columns:
        op.add_column(
            "telegram_messages",
            sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        )
        op.create_index(
            "ix_telegram_messages_content_fingerprint",
            "telegram_messages",
            ["content_fingerprint"],
        )
    if "duplicate_of_message_id" not in columns:
        op.add_column(
            "telegram_messages",
            sa.Column("duplicate_of_message_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_telegram_messages_duplicate_of",
            "telegram_messages",
            "telegram_messages",
            ["duplicate_of_message_id"],
            ["id"],
        )
        op.create_index(
            "ix_telegram_messages_duplicate_of_message_id",
            "telegram_messages",
            ["duplicate_of_message_id"],
        )
    if "classification_result_json" not in columns:
        op.add_column(
            "telegram_messages",
            sa.Column("classification_result_json", sa.JSON(), nullable=True),
        )


def downgrade():
    op.drop_column("telegram_messages", "classification_result_json")
    op.drop_index(
        "ix_telegram_messages_duplicate_of_message_id",
        table_name="telegram_messages",
    )
    op.drop_constraint(
        "fk_telegram_messages_duplicate_of",
        "telegram_messages",
        type_="foreignkey",
    )
    op.drop_column("telegram_messages", "duplicate_of_message_id")
    op.drop_index(
        "ix_telegram_messages_content_fingerprint",
        table_name="telegram_messages",
    )
    op.drop_column("telegram_messages", "content_fingerprint")
