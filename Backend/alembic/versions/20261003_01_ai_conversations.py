"""Create persistent AI and agent sessions.

Revision ID: 20261003_01
Revises:
Create Date: 2026-10-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20261003_01"
down_revision = "20261002_00"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if all(
        _has_table(name)
        for name in (
            "ai_conversations",
            "ai_conversation_messages",
            "agent_task_records",
        )
    ):
        return

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=160),
            nullable=False,
            server_default="New conversation",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_conversations_id",
        "ai_conversations",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_project_id",
        "ai_conversations",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_user_id",
        "ai_conversations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_updated_at",
        "ai_conversations",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_user_project_updated",
        "ai_conversations",
        ["user_id", "project_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_conversation_messages_id",
        "ai_conversation_messages",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversation_messages_conversation_id",
        "ai_conversation_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversation_messages_conversation_created",
        "ai_conversation_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "agent_task_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_task_records_project_id",
        "agent_task_records",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_task_records_user_id",
        "agent_task_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_task_records_status",
        "agent_task_records",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_task_records_updated_at",
        "agent_task_records",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_task_records_user_project",
        "agent_task_records",
        ["user_id", "project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_task_records_user_project",
        table_name="agent_task_records",
    )
    op.drop_index(
        "ix_agent_task_records_updated_at",
        table_name="agent_task_records",
    )
    op.drop_index(
        "ix_agent_task_records_status",
        table_name="agent_task_records",
    )
    op.drop_index(
        "ix_agent_task_records_user_id",
        table_name="agent_task_records",
    )
    op.drop_index(
        "ix_agent_task_records_project_id",
        table_name="agent_task_records",
    )
    op.drop_table("agent_task_records")
    op.drop_index(
        "ix_ai_conversation_messages_conversation_created",
        table_name="ai_conversation_messages",
    )
    op.drop_index(
        "ix_ai_conversation_messages_conversation_id",
        table_name="ai_conversation_messages",
    )
    op.drop_index(
        "ix_ai_conversation_messages_id",
        table_name="ai_conversation_messages",
    )
    op.drop_table("ai_conversation_messages")
    op.drop_index(
        "ix_ai_conversations_user_project_updated",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_updated_at",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_user_id",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_project_id",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_id",
        table_name="ai_conversations",
    )
    op.drop_table("ai_conversations")
