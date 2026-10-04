from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from App.database.database import Base


class RepositoryIndexJob(Base):
    __tablename__ = "repository_index_jobs"

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
