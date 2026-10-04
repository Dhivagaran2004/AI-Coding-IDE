"""Create the core application tables required by later migrations.

Revision ID: 20261002_00
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_00"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _create_index_if_missing(
    name: str,
    table: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
    }
    if name not in indexes:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_users_id", "users", ["id"])
    _create_index_if_missing("ix_users_email", "users", ["email"], unique=True)

    if not _has_table("projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("language", sa.String(length=50), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_projects_id", "projects", ["id"])

    if not _has_table("project_files"):
        op.create_table(
            "project_files",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("parent_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("type", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("language", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"], ["projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["parent_id"], ["project_files.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_project_files_id", "project_files", ["id"])
    _create_index_if_missing(
        "ix_project_files_project_id", "project_files", ["project_id"]
    )
    _create_index_if_missing(
        "ix_project_files_parent_id", "project_files", ["parent_id"]
    )
    _create_index_if_missing(
        "ix_project_files_project_parent",
        "project_files",
        ["project_id", "parent_id"],
    )

    if not _has_table("repository_indexes"):
        op.create_table(
            "repository_indexes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("file_id", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error_message", sa.String(length=500), nullable=True),
            sa.Column("indexed_content", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"], ["projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["file_id"], ["project_files.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        "ix_repository_indexes_id", "repository_indexes", ["id"]
    )
    _create_index_if_missing(
        "ix_repository_indexes_project_id",
        "repository_indexes",
        ["project_id"],
    )
    _create_index_if_missing(
        "ix_repository_indexes_file_id", "repository_indexes", ["file_id"]
    )
    _create_index_if_missing(
        "ix_repository_indexes_project_file",
        "repository_indexes",
        ["project_id", "file_id"],
    )

    if not _has_table("repository_chunks"):
        op.create_table(
            "repository_chunks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("file_id", sa.Integer(), nullable=False),
            sa.Column("file_path", sa.String(length=2000), nullable=False),
            sa.Column("language", sa.String(length=100), nullable=True),
            sa.Column("start_line", sa.Integer(), nullable=False),
            sa.Column("end_line", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("embedding", sa.JSON(), nullable=False),
            sa.Column("chunk_metadata", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(
                ["project_id"], ["projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["file_id"], ["project_files.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        "ix_repository_chunks_id", "repository_chunks", ["id"]
    )
    _create_index_if_missing(
        "ix_repository_chunks_project_file",
        "repository_chunks",
        ["project_id", "file_id"],
    )


def downgrade() -> None:
    # This adoption baseline may have encountered tables that predate Alembic.
    # Keeping it irreversible avoids deleting existing project/user data.
    pass
