from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from App.database.database import Base


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(160), nullable=False, default="New conversation")
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )

    messages = relationship(
        "AIConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AIConversationMessage.created_at",
    )


class AIConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    message_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    conversation = relationship("AIConversation", back_populates="messages")


Index(
    "ix_ai_conversations_user_project_updated",
    AIConversation.user_id,
    AIConversation.project_id,
    AIConversation.updated_at,
)
Index(
    "ix_ai_conversation_messages_conversation_created",
    AIConversationMessage.conversation_id,
    AIConversationMessage.created_at,
)
