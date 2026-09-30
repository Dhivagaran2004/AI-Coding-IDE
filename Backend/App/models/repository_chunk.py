from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)

from App.database.database import Base


class RepositoryChunk(Base):
    __tablename__ = "repository_chunks"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id = Column(
        Integer,
        ForeignKey("project_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path = Column(String(2000), nullable=False)
    language = Column(String(100), nullable=True)
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    embedding = Column(JSON, nullable=False)
    chunk_metadata = Column(
        JSON,
        nullable=False,
        default=dict,
    )


Index(
    "ix_repository_chunks_project_file",
    RepositoryChunk.project_id,
    RepositoryChunk.file_id,
)