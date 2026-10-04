from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)

from App.database.database import Base


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id = Column(
        Integer,
        ForeignKey("ai_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider = Column(String(80), nullable=False)
    model = Column(String(255), nullable=False)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    reserved_tokens = Column(Integer, nullable=False, default=0)
    token_count_source = Column(
        String(20),
        nullable=False,
        default="estimated",
    )
    request_status = Column(String(20), nullable=False, default="pending")
    error_status = Column(String(80), nullable=True)


Index("ix_ai_usage_user_requested", AIUsage.user_id, AIUsage.requested_at)
Index("ix_ai_usage_project_requested", AIUsage.project_id, AIUsage.requested_at)