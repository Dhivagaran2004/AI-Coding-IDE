from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    Index,
)

from App.database.database import Base


class RepositoryIndex(Base):
    __tablename__ = "repository_indexes"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    project_id = Column(
        Integer,
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    file_id = Column(
        Integer,
        ForeignKey(
            "project_files.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    content_hash = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="pending",
    )

    indexed_content = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


Index(
    "ix_repository_indexes_project_file",
    RepositoryIndex.project_id,
    RepositoryIndex.file_id,
)