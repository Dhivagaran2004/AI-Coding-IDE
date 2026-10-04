"""Track AI requests and token reservations.

Revision ID: 20261003_03
Revises: 20261003_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261003_03"
down_revision = "20261003_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ai_usage"):
        return

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "token_count_source",
            sa.String(length=20),
            nullable=False,
            server_default="estimated",
        ),
        sa.Column("request_status", sa.String(length=20), nullable=False),
        sa.Column("error_status", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
    op.create_index("ix_ai_usage_project_id", "ai_usage", ["project_id"])
    op.create_index(
        "ix_ai_usage_conversation_id", "ai_usage", ["conversation_id"]
    )
    op.create_index(
        "ix_ai_usage_user_requested", "ai_usage", ["user_id", "requested_at"]
    )
    op.create_index(
        "ix_ai_usage_project_requested",
        "ai_usage",
        ["project_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_project_requested", table_name="ai_usage")
    op.drop_index("ix_ai_usage_user_requested", table_name="ai_usage")
    op.drop_index("ix_ai_usage_conversation_id", table_name="ai_usage")
    op.drop_index("ix_ai_usage_project_id", table_name="ai_usage")
    op.drop_index("ix_ai_usage_user_id", table_name="ai_usage")
    op.drop_table("ai_usage")