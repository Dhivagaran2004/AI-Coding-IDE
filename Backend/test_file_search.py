from App.service.AI.search.file_search import FileSearchService


class FakeProjectFile:
    """
    Small test object that behaves like ProjectFile.
    """

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
    """
    Minimal fake SQLAlchemy query.
    """

    def __init__(self, files):
        self.files = files

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.files

    def first(self):
        return self.files[0] if self.files else None


class FakeDB:
    """
    Minimal fake database session.
    """

    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


def create_service(files):
    return FileSearchService(
        db=FakeDB(files),
        project_id=1,
    )


# =========================================================
# BASIC SEARCH
# =========================================================


def test_search_finds_file_by_name():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="def login(): pass",
        ),
        FakeProjectFile(
            id=2,
            name="database.py",
            content="def connect(): pass",
        ),
    ]

    service = create_service(files)

    results = service.search("auth.py")

    assert len(results) == 1
    assert results[0].name == "auth.py"


def test_search_finds_file_by_content():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="""
def login(username, password):
    authenticate_user(username, password)
""",
        ),
        FakeProjectFile(
            id=2,
            name="database.py",
            content="def connect(): pass",
        ),
    ]

    service = create_service(files)

    results = service.search("authenticate_user")

    assert len(results) == 1
    assert results[0].name == "auth.py"


# =========================================================
# PATH SEARCH
# =========================================================


def test_search_finds_file_using_path():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="def login(): pass",
        ),
        FakeProjectFile(
            id=2,
            name="utils.py",
            content="def helper(): pass",
        ),
    ]

    service = create_service(files)

    # Directly verify path generation.
    path = service._get_file_path(files[0])

    assert path == "auth.py"


# =========================================================
# RANKING
# =========================================================


def test_filename_match_scores_higher_than_content_match():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="some unrelated code",
        ),
        FakeProjectFile(
            id=2,
            name="user.py",
            content="auth authentication login",
        ),
    ]

    service = create_service(files)

    results = service.search("auth")

    assert len(results) == 2
    assert results[0].name == "auth.py"
    assert results[0].score > results[1].score


# =========================================================
# CASE INSENSITIVE SEARCH
# =========================================================


def test_search_is_case_insensitive():
    files = [
        FakeProjectFile(
            id=1,
            name="AuthService.py",
            content="class AuthService:",
        ),
    ]

    service = create_service(files)

    results = service.search("authservice")

    assert len(results) == 1
    assert results[0].name == "AuthService.py"


# =========================================================
# IGNORED FILES
# =========================================================


def test_env_file_is_ignored():
    files = [
        FakeProjectFile(
            id=1,
            name=".env",
            content="SECRET_KEY=super-secret",
        ),
        FakeProjectFile(
            id=2,
            name="config.py",
            content="SECRET_KEY = settings.SECRET_KEY",
        ),
    ]

    service = create_service(files)

    results = service.search("SECRET_KEY")

    assert all(
        result.name != ".env"
        for result in results
    )


def test_binary_file_is_ignored():
    files = [
        FakeProjectFile(
            id=1,
            name="logo.png",
            content="binary-data",
        ),
        FakeProjectFile(
            id=2,
            name="app.py",
            content="load logo",
        ),
    ]

    service = create_service(files)

    results = service.search("logo")

    assert all(
        result.name != "logo.png"
        for result in results
    )


# =========================================================
# EMPTY CONTENT
# =========================================================


def test_empty_content_file_is_ignored():
    files = [
        FakeProjectFile(
            id=1,
            name="empty.py",
            content="",
        ),
        FakeProjectFile(
            id=2,
            name="app.py",
            content="application code",
        ),
    ]

    service = create_service(files)

    results = service.search("application")

    assert len(results) == 1
    assert results[0].name == "app.py"


# =========================================================
# EMPTY QUERY
# =========================================================


def test_empty_query_returns_empty_list():
    files = [
        FakeProjectFile(
            id=1,
            name="app.py",
            content="application code",
        ),
    ]

    service = create_service(files)

    results = service.search("")

    assert results == []


def test_whitespace_query_returns_empty_list():
    files = [
        FakeProjectFile(
            id=1,
            name="app.py",
            content="application code",
        ),
    ]

    service = create_service(files)

    results = service.search("   ")

    assert results == []


# =========================================================
# RESULT LIMIT
# =========================================================


def test_search_respects_limit():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication",
        ),
        FakeProjectFile(
            id=2,
            name="user.py",
            content="authentication",
        ),
        FakeProjectFile(
            id=3,
            name="login.py",
            content="authentication",
        ),
    ]

    service = create_service(files)

    results = service.search(
        "authentication",
        limit=2,
    )

    assert len(results) == 2


def test_zero_limit_returns_empty_list():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="authentication",
        ),
    ]

    service = create_service(files)

    results = service.search(
        "authentication",
        limit=0,
    )

    assert results == []


# =========================================================
# PATH BUILDING
# =========================================================


def test_nested_file_path_is_generated():
    folder = FakeProjectFile(
        id=10,
        name="services",
        file_type="folder",
    )

    file = FakeProjectFile(
        id=11,
        name="auth.py",
        content="login code",
        parent_id=10,
    )

    service = create_service(
        [folder, file]
    )

    path = service._get_file_path(file)

    assert path == "services/auth.py"


# =========================================================
# PROJECT ISOLATION
# =========================================================


def test_search_service_uses_project_id():
    files = [
        FakeProjectFile(
            id=1,
            name="auth.py",
            content="login",
        ),
    ]

    service = FileSearchService(
        db=FakeDB(files),
        project_id=42,
    )

    assert service.project_id == 42