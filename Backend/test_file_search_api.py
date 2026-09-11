from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api.file_search_router import router


# =========================================================
# TEST APP
# =========================================================

app = FastAPI()

app.include_router(router)

client = TestClient(app)


# =========================================================
# FAKE PROJECT FILE
# =========================================================


class FakeProjectFile:
    def __init__(
        self,
        id,
        name,
        file_type="file",
        content="",
        language=None,
        parent_id=None,
    ):
        self.id = id
        self.name = name
        self.type = file_type
        self.content = content
        self.language = language
        self.parent_id = parent_id


# =========================================================
# FAKE QUERY
# =========================================================


class FakeQuery:
    def __init__(self, files):
        self.files = files

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.files

    def first(self):
        if self.files:
            return self.files[0]

        return None


# =========================================================
# FAKE DATABASE
# =========================================================


class FakeDB:
    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


# =========================================================
# OVERRIDE DATABASE DEPENDENCY
# =========================================================


def override_get_db():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="""
def login(username, password):
    authenticate_user(username, password)
""",
            language="python",
        ),
        FakeProjectFile(
            id=2,
            name="database.py",
            content="""
def connect_database():
    pass
""",
            language="python",
        ),
        FakeProjectFile(
            id=3,
            name="main.py",
            content="""
from auth import login
from database import connect_database
""",
            language="python",
        ),
        FakeProjectFile(
            id=4,
            name=".env",
            content="SECRET_KEY=super-secret",
        ),
        FakeProjectFile(
            id=5,
            name="logo.png",
            content="binary-data",
        ),
    ]

    yield FakeDB(files)


# We need the same dependency object used by the router.
from App.database.database import get_db

app.dependency_overrides[get_db] = override_get_db


# =========================================================
# BASIC SEARCH
# =========================================================


def test_search_api_returns_results():
    response = client.get(
        "/projects/1/search?q=authenticate_user"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["project_id"] == 1
    assert data["query"] == "authenticate_user"
    assert data["count"] == 1

    assert data["results"][0]["name"] == "auth.py"


# =========================================================
# RESPONSE STRUCTURE
# =========================================================


def test_search_api_response_contains_expected_fields():
    response = client.get(
        "/projects/1/search?q=login"
    )

    assert response.status_code == 200

    result = response.json()["results"][0]

    assert "file_id" in result
    assert "name" in result
    assert "path" in result
    assert "content" in result
    assert "language" in result
    assert "score" in result


# =========================================================
# FILENAME SEARCH
# =========================================================


def test_search_api_finds_filename():
    response = client.get(
        "/projects/1/search?q=auth.py"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 1
    assert data["results"][0]["name"] == "auth.py"


# =========================================================
# CASE INSENSITIVE SEARCH
# =========================================================


def test_search_api_is_case_insensitive():
    response = client.get(
        "/projects/1/search?q=AUTH.PY"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 1
    assert data["results"][0]["name"] == "auth.py"


# =========================================================
# LIMIT
# =========================================================


def test_search_api_respects_limit():
    response = client.get(
        "/projects/1/search?q=python&limit=2"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["results"]) <= 2


# =========================================================
# EMPTY QUERY
# =========================================================


def test_search_api_rejects_empty_query():
    response = client.get(
        "/projects/1/search?q="
    )

    assert response.status_code == 422


# =========================================================
# MISSING QUERY
# =========================================================


def test_search_api_requires_query():
    response = client.get(
        "/projects/1/search"
    )

    assert response.status_code == 422


# =========================================================
# INVALID LIMIT
# =========================================================


def test_search_api_rejects_zero_limit():
    response = client.get(
        "/projects/1/search?q=login&limit=0"
    )

    assert response.status_code == 422


def test_search_api_rejects_limit_above_maximum():
    response = client.get(
        "/projects/1/search?q=login&limit=51"
    )

    assert response.status_code == 422


# =========================================================
# IGNORED FILES
# =========================================================


def test_search_api_does_not_return_env_file():
    response = client.get(
        "/projects/1/search?q=SECRET_KEY"
    )

    assert response.status_code == 200

    data = response.json()

    assert all(
        result["name"] != ".env"
        for result in data["results"]
    )


def test_search_api_does_not_return_binary_file():
    response = client.get(
        "/projects/1/search?q=logo"
    )

    assert response.status_code == 200

    data = response.json()

    assert all(
        result["name"] != "logo.png"
        for result in data["results"]
    )


# =========================================================
# PROJECT ID
# =========================================================


def test_search_api_accepts_project_id():
    response = client.get(
        "/projects/999/search?q=login"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["project_id"] == 999