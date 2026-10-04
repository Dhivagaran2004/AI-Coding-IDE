from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.ai_conversation import AIConversation
from App.models.project import Project
from App.models.user import User
from App.schema.ai_conversation_schema import (
    AIConversationCreate,
    AIConversationDetail,
    AIConversationRename,
    AIConversationSummary,
)


router = APIRouter(prefix="/ai/conversations", tags=["AI Conversations"])


def _owned_conversation(
    db: Session,
    conversation_id: int,
    user_id: int,
) -> AIConversation:
    conversation = (
        db.query(AIConversation)
        .options(selectinload(AIConversation.messages))
        .filter(
            AIConversation.id == conversation_id,
            AIConversation.user_id == user_id,
        )
        .first()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if conversation.project_id is not None:
        owned_project = (
            db.query(Project.id)
            .filter(
                Project.id == conversation.project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if owned_project is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.get("", response_model=list[AIConversationSummary])
def list_conversations(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(AIConversation).filter(
        AIConversation.user_id == current_user.id
    )
    if project_id is not None:
        query = query.filter(AIConversation.project_id == project_id)
    return query.order_by(AIConversation.updated_at.desc()).all()


@router.post("", response_model=AIConversationSummary, status_code=201)
def create_conversation(
    request: AIConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if request.project_id is not None:
        project = (
            db.query(Project)
            .filter(
                Project.id == request.project_id,
                Project.user_id == current_user.id,
            )
            .first()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
    conversation = AIConversation(
        project_id=request.project_id,
        user_id=current_user.id,
        title=request.title or "New conversation",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=AIConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _owned_conversation(db, conversation_id, current_user.id)


@router.patch("/{conversation_id}", response_model=AIConversationSummary)
def rename_conversation(
    conversation_id: int,
    request: AIConversationRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = _owned_conversation(db, conversation_id, current_user.id)
    conversation.title = request.title.strip()
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = _owned_conversation(db, conversation_id, current_user.id)
    db.delete(conversation)
    db.commit()


@router.delete("/{conversation_id}/messages", status_code=204)
def clear_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = _owned_conversation(db, conversation_id, current_user.id)
    conversation.messages.clear()
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
