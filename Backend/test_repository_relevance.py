from App.service.AI.context.repository_relevance import (
    RepositoryRelevanceService,
)


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


class FakeDB:
    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


def create_service(
    files,
    max_chars=None,
    max_files=None,
):
    return RepositoryRelevanceService(
        db=FakeDB(files),
        project_id=1,
        max_chars=max_chars,
        max_files=max_files,
    )


# =========================================================
# SEARCH + SELECTION
# =========================================================


def test_build_context_searches_and_selects_files():
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
    ]

    service = create_service(files)

    result = service.build_context(
        "authenticate_user"
    )

    assert result.search_result_count == 1
    assert result.selected_file_count == 1

    assert (
        result.selected_files[0].name
        == "auth.py"
    )

    assert "authenticate_user" in result.content


# =========================================================
# EMPTY QUERY
# =========================================================


def test_empty_query_returns_empty_result():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="login code",
        ),
    ]

    service = create_service(files)

    result = service.build_context("")

    assert result.content == ""
    assert result.search_results == []
    assert result.selected_files == []
    assert result.search_result_count == 0
    assert result.selected_file_count == 0


# =========================================================
# MAX FILES
# =========================================================


def test_context_selection_respects_max_files():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication login",
        ),
        FakeProjectFile(
            id=2,
            name="user.py",
            content="authentication user",
        ),
        FakeProjectFile(
            id=3,
            name="login.py",
            content="authentication login",
        ),
    ]

    service = create_service(
        files,
        max_files=2,
    )

    result = service.build_context(
        "authentication"
    )

    assert result.selected_file_count <= 2
    assert len(result.selected_files) <= 2


# =========================================================
# MAX CHARACTERS
# =========================================================


def test_context_selection_respects_max_chars():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication " * 100,
        ),
        FakeProjectFile(
            id=2,
            name="user.py",
            content="authentication " * 100,
        ),
    ]

    service = create_service(
        files,
        max_chars=500,
        max_files=10,
    )

    result = service.build_context(
        "authentication"
    )

    assert result.character_count <= 500


# =========================================================
# SEARCH LIMIT
# =========================================================


def test_search_limit_is_respected():
    files = [
        FakeProjectFile(
            id=i,
            name=f"file{i}.py",
            content="authentication code",
        )
        for i in range(1, 11)
    ]

    service = create_service(files)

    result = service.build_context(
        "authentication",
        search_limit=3,
    )

    assert result.search_result_count <= 3


# =========================================================
# INVALID SEARCH LIMIT
# =========================================================


def test_invalid_search_limit_is_rejected():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication",
        ),
    ]

    service = create_service(files)

    try:
        service.build_context(
            "authentication",
            search_limit=0,
        )
        assert False
    except ValueError:
        assert True


# =========================================================
# RESULT CONTENT
# =========================================================


def test_result_contains_structured_context():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="def login(): pass",
            language="python",
        ),
    ]

    service = create_service(files)

    result = service.build_context(
        "login"
    )

    assert (
        "RELEVANT REPOSITORY CONTEXT"
        in result.content
    )

    assert (
        "--- FILE: auth.py ---"
        in result.content
    )

    assert (
        "LANGUAGE: python"
        in result.content
    )


# =========================================================
# CHARACTER COUNT
# =========================================================


def test_character_count_matches_content():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="def login(): pass",
        ),
    ]

    service = create_service(files)

    result = service.build_context(
        "login"
    )

    assert (
        result.character_count
        == len(result.content)
    )
def test_ranker_is_used_before_context_selection():
    files = [
        FakeProjectFile(
            id=1,
            name="database.py",
            content="database connection",
        ),
        FakeProjectFile(
            id=2,
            name="auth.py",
            content="authentication login",
        ),
    ]

    service = create_service(files)

    result = service.build_context(
        "authentication",
    )

    assert result.selected_file_count >= 1

    assert "auth.py" in result.content


def test_relevant_file_is_ranked_higher():
    files = [
        FakeProjectFile(
            id=1,
            name="database.py",
            content="database database",
        ),
        FakeProjectFile(
            id=2,
            name="authentication.py",
            content="authentication authentication",
        ),
    ]

    service = create_service(files)

    result = service.build_context(
        "authentication",
    )

    assert result.selected_file_count >= 1

    assert "authentication.py" in result.content


def test_ranking_does_not_break_max_files():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication",
        ),
        FakeProjectFile(
            id=2,
            name="login.py",
            content="authentication login",
        ),
        FakeProjectFile(
            id=3,
            name="user.py",
            content="authentication user",
        ),
        FakeProjectFile(
            id=4,
            name="database.py",
            content="authentication database",
        ),
    ]

    service = create_service(
        files,
        max_files=2,
    )

    result = service.build_context(
        "authentication",
    )

    assert result.selected_file_count <= 2