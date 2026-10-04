from fastapi import FastAPI
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from App.database.database import engine, Base, get_db

from App.models.user import User
from App.models.project import Project
from App.models.ai_conversation import AIConversation, AIConversationMessage

from App.routes.auth import router as auth_router
from App.routes.User import router as user_router
from App.routes.project import router as project_router
from App.routes.project_file import router as project_file_router
from fastapi.middleware.cors import CORSMiddleware
from App.api.terminal_router import router as terminal_router
from App.api.ai_router import ai_service, router as ai_router
from App.api.file_search_router import router as file_search_router
from App.api.repository_index_router import router as repository_index_router
from App.api.agent_router import router as agent_router
from App.api.conversation_router import router as conversation_router
from App.api.git_router import router as git_router

Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="AI Coding IDE",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(user_router)
app.include_router(project_router)
app.include_router(project_file_router)
app.include_router(terminal_router)
app.include_router(ai_router)
app.include_router(file_search_router)
app.include_router(repository_index_router)
app.include_router(agent_router)
app.include_router(conversation_router)
app.include_router(git_router)

@app.get("/")
def home():

    return {
        "message": "AI Coding IDE Backend Running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "components": {"database": "unavailable"},
            },
        ) from error
    provider_state = "configured" if ai_service is not None else "unavailable"
    return {
        "status": "ok" if ai_service is not None else "degraded",
        "components": {
            "database": "ready",
            "ai_provider": provider_state,
        },
    }