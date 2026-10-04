from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from App.database.database import get_db
from main import app


class ReadyDatabase:
    def execute(self, statement):
        return 1


class UnavailableDatabase:
    def execute(self, statement):
        raise SQLAlchemyError("private database details")


def test_health_liveness_and_readiness_hide_infrastructure_details(monkeypatch):
    def override_ready_db():
        yield ReadyDatabase()

    monkeypatch.setitem(app.dependency_overrides, get_db, override_ready_db)
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    readiness = client.get("/health/ready")
    assert readiness.status_code == 200
    assert readiness.json()["components"]["database"] == "ready"
    assert readiness.json()["components"]["ai_provider"] in {
        "configured",
        "unavailable",
    }


def test_readiness_returns_controlled_database_failure(monkeypatch):
    def override_failed_db():
        yield UnavailableDatabase()

    monkeypatch.setitem(app.dependency_overrides, get_db, override_failed_db)
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "degraded",
        "components": {"database": "unavailable"},
    }
    assert "private database details" not in response.text
