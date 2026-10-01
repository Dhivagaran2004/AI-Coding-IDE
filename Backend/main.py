from fastapi import FastAPI

from App.database.database import engine, Base

from App.models.user import User
from App.models.project import Project

from App.routes.auth import router as auth_router
from App.routes.User import router as user_router
from App.routes.project import router as project_router
from App.routes.project_file import router as project_file_router
from fastapi.middleware.cors import CORSMiddleware
from App.api.terminal_router import router as terminal_router
from App.api.ai_router import router as ai_router
from App.api.file_search_router import router as file_search_router
from App.api.repository_index_router import router as repository_index_router
from App.api.agent_router import router as agent_router

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

@app.get("/")
def home():

    return {
        "message": "AI Coding IDE Backend Running"
    }