from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Text

from App.database.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentTaskRecord(Base):
    __tablename__ = "agent_task_records"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    state = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        index=True,
    )


Index("ix_agent_task_records_user_project", AgentTaskRecord.user_id, AgentTaskRecord.project_id)
