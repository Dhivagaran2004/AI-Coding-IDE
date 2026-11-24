"""Store canonical external workspace paths on projects.

Revision ID: 20261123_07
Revises: 20261119_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20261123_07"
down_revision = "20261119_06"
branch_labels = None
depends_on = None


def _project_columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("projects")
    }


def _project_indexes() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("projects")
    }


def upgrade() -> None:
    if "workspace_path" not in _project_columns():
        op.add_column(
            "projects",
            sa.Column("workspace_path", sa.String(length=2000), nullable=True),
        )
    if "ix_projects_workspace_path" not in _project_indexes():
        op.create_index(
            "ix_projects_workspace_path",
            "projects",
            ["workspace_path"],
            unique=True,
        )


def downgrade() -> None:
    if "ix_projects_workspace_path" in _project_indexes():
        op.drop_index("ix_projects_workspace_path", table_name="projects")
    if "workspace_path" in _project_columns():
        op.drop_column("projects", "workspace_path")
