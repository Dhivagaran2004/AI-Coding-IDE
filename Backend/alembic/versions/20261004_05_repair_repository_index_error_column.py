"""Ensure repository index failure details have a database column.

Revision ID: 20261004_05
Revises: 20261004_04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261004_05"
down_revision = "20261004_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("repository_indexes"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("repository_indexes")
    }
    if "error_message" not in columns:
        op.add_column(
            "repository_indexes",
            sa.Column("error_message", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("repository_indexes"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("repository_indexes")
    }
    if "error_message" in columns:
        op.drop_column("repository_indexes", "error_message")
