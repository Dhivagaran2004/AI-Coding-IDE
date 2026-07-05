from fastapi import FastAPI

from App.database.database import engine, Base
# Import models so SQLAlchemy knows about them
from App.models.user import User

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Coding IDE",
    version="1.0.0"
)


@app.get("/")
def home():
    return {
        "message": "AI Coding IDE Backend Running"
    }

