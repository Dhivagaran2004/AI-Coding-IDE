import pytest

from App.service.AI.context.repository_question_service import (
    RepositoryQuestionService,
)
from App.service.AI.context.repository_relevance import (
    RepositoryRelevanceResult,
)


class FakeFile:
    def __init__(self, file_id, name, content, language="python"):
        self.id = file_id
        self.project_id = 1
        self.parent_id = None
        self.name = name
        self.content = content
        self.language = language
        self.type = "file"


class FakeQuery:
    def __init__(self, files):
        self.files = files

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.files


class FakeDB:
    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


def test_question_finds_project_creation_file():
    files = [
        FakeFile(
            1,
            "project.py",
            "def create_project():\n    return Project()",
        ),
        FakeFile(2, "terminal.py", "def execute_command(): pass"),
    ]
    service = RepositoryQuestionService(FakeDB(files), project_id=1)

    result = service.build_context("Which file creates projects?")

    assert result.query == "Which file creates projects?"
    assert any(item.name == "project.py" for item in result.selected_files)
    assert "create_project" in result.content


def test_question_finds_authentication_using_related_word_form():
    files = [
        FakeFile(
            1,
            "auth.py",
            "def authenticate_user(credentials):\n    return True",
        ),
        FakeFile(2, "terminal.py", "def execute_command(): pass"),
    ]
    service = RepositoryQuestionService(FakeDB(files), project_id=1)

    result = service.build_context("Where is authentication handled?")

    assert any(item.name == "auth.py" for item in result.selected_files)
    assert "authenticate_user" in result.content


def test_multi_file_question_selects_frontend_and_backend():
    files = [
        FakeFile(
            1,
            "LoginPage.tsx",
            "frontend login form submits credentials to the API",
            language="typescript",
        ),
        FakeFile(
            2,
            "auth.py",
            "backend login route validates credentials",
        ),
        FakeFile(3, "terminal.py", "execute terminal commands"),
    ]
    service = RepositoryQuestionService(FakeDB(files), project_id=1)

    result = service.build_context(
        "Explain the login flow from frontend to backend."
    )

    selected_names = {item.name for item in result.selected_files}
    assert {"LoginPage.tsx", "auth.py"} <= selected_names
    assert "terminal.py" not in selected_names


def test_question_service_accepts_alternative_context_provider():
    class FakeContextProvider:
        query = None

        def build_context(self, query, search_limit=None):
            self.query = query
            return RepositoryRelevanceResult(
                query=query,
                content="provided context",
                search_results=[],
                selected_files=[],
                search_result_count=0,
                selected_file_count=0,
                character_count=len("provided context"),
            )

    provider = FakeContextProvider()
    service = RepositoryQuestionService(
        db=None,
        project_id=1,
        context_provider=provider,
    )

    result = service.build_context("Which file creates projects?")

    assert provider.query == "create project"
    assert result.query == "Which file creates projects?"
    assert result.content == "provided context"


@pytest.mark.parametrize(
    ("question", "file_name", "file_content"),
    [
        (
            "Explain the project structure.",
            "architecture.md",
            "project structure describes backend frontend api services and models",
        ),
        (
            "Explain the backend architecture.",
            "main.py",
            "backend architecture uses FastAPI routes services and models",
        ),
        (
            "Explain the frontend architecture.",
            "App.tsx",
            "frontend architecture uses pages components and services",
        ),
        (
            "Explain the API flow.",
            "ai_router.py",
            "API flow connects the request to the service and response",
        ),
        (
            "How does terminal execution work?",
            "terminal_service.py",
            "terminal execution runs commands in the project directory",
        ),
        (
            "Where is the AI provider configured?",
            "provider_factory.py",
            "AI provider configuration selects the Qwen provider",
        ),
        (
            "Explain repository relationships.",
            "project_file.py",
            "repository relationships connect project files through parent ids",
        ),
    ],
)
def test_project_understanding_questions_retrieve_matching_files(
    question,
    file_name,
    file_content,
):
    service = RepositoryQuestionService(
        FakeDB([FakeFile(1, file_name, file_content)]),
        project_id=1,
    )

    result = service.build_context(question)

    assert [item.name for item in result.selected_files] == [file_name]
    assert file_content in result.content