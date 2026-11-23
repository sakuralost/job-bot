"""manual decisions take priority over model filtering"""

from alembic import op


revision = "0006_manual_status_priority"
down_revision = "0005_manual_scan_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE job_posts AS job
        SET status = dedup.status,
            is_excluded = false,
            excluded_reason = NULL
        FROM job_dedup_groups AS dedup
        WHERE job.dedup_group_id = dedup.id
          AND dedup.status IN ('saved', 'ignored', 'contacted')
          AND (
              job.status <> dedup.status
              OR job.is_excluded = true
          )
        """
    )


def downgrade():
    # 人工状态属于用户数据，降级结构时也不能反向覆盖。
    pass
