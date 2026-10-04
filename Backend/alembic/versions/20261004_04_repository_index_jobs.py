"""Add safe project-level repository indexing jobs.

Revision ID: 20261004_04
Revises: 20261003_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20261004_04"
down_revision = "20261003_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("repository_indexes"):
        columns = {column["name"] for column in inspector.get_columns("repository_indexes")}
        if "error_message" not in columns:
            op.add_column(
                "repository_indexes",
                sa.Column("error_message", sa.String(length=500), nullable=True),
            )
    if not inspector.has_table("repository_index_jobs"):
        op.create_table(
            "repository_index_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"], ["projects.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id"),
        )
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("repository_index_jobs")
    }
    if "ix_repository_index_jobs_project_id" not in indexes:
        op.create_index(
            "ix_repository_index_jobs_project_id",
            "repository_index_jobs",
            ["project_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_index_jobs_project_id",
        table_name="repository_index_jobs",
    )
    op.drop_table("repository_index_jobs")
    op.drop_column("repository_indexes", "error_message")
