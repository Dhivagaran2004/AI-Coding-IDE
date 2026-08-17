from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey
)

from sqlalchemy.orm import relationship

from App.database.database import Base


class ProjectFile(Base):
    __tablename__ = "project_files"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    parent_id = Column(
        Integer,
        ForeignKey("project_files.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    name = Column(
        String(255),
        nullable=False
    )

    type = Column(
        String(20),
        nullable=False
    )

    content = Column(
        Text,
        nullable=True
    )

    language = Column(
        String(50),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relationship with Project
    project = relationship(
        "Project",
        back_populates="files"
    )

    # Parent folder
    parent = relationship(
        "ProjectFile",
        remote_side=[id],
        back_populates="children"
    )

    # Child files/folders
    children = relationship(
        "ProjectFile",
        back_populates="parent",
        cascade="all, delete-orphan"
    )