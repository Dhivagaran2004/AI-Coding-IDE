from App.service.AI.context.relevant_context import (
    RelevantContextSelector,
)
from App.service.AI.search.file_search import (
    FileSearchResult,
)


def make_result(
    file_id: int,
    name: str,
    content: str,
    score: float,
    language: str | None = "python",
    path: str | None = None,
):
    return FileSearchResult(
        file_id=file_id,
        name=name,
        path=path or name,
        content=content,
        language=language,
        score=score,
    )


# =========================================================
# BASIC SELECTION
# =========================================================


def test_select_returns_relevant_files():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
        ),
        make_result(
            2,
            "database.py",
            "def connect(): pass",
            50,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="login",
        results=results,
    )

    assert selected.file_count == 2
    assert selected.files[0].name == "auth.py"
    assert selected.files[1].name == "database.py"


# =========================================================
# RANKING
# =========================================================


def test_select_orders_files_by_score():
    results = [
        make_result(
            1,
            "low.py",
            "low relevance",
            10,
        ),
        make_result(
            2,
            "high.py",
            "high relevance",
            100,
        ),
        make_result(
            3,
            "medium.py",
            "medium relevance",
            50,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="relevance",
        results=results,
    )

    assert [
        file.name
        for file in selected.files
    ] == [
        "high.py",
        "medium.py",
        "low.py",
    ]


# =========================================================
# MAX FILES
# =========================================================


def test_select_respects_max_files():
    results = [
        make_result(
            i,
            f"file{i}.py",
            "some code",
            100 - i,
        )
        for i in range(1, 11)
    ]

    selector = RelevantContextSelector(
        max_files=3,
    )

    selected = selector.select(
        query="code",
        results=results,
    )

    assert selected.file_count == 3
    assert len(selected.files) == 3


# =========================================================
# MAX CHARACTERS
# =========================================================


def test_select_respects_max_characters():
    results = [
        make_result(
            1,
            "first.py",
            "a" * 100,
            100,
        ),
        make_result(
            2,
            "second.py",
            "b" * 100,
            90,
        ),
    ]

    selector = RelevantContextSelector(
        max_chars=250,
        max_files=10,
    )

    selected = selector.select(
        query="test",
        results=results,
    )

    assert selected.character_count <= 250


# =========================================================
# EMPTY QUERY
# =========================================================


def test_empty_query_returns_empty_context():
    results = [
        make_result(
            1,
            "auth.py",
            "login code",
            100,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="",
        results=results,
    )

    assert selected.file_count == 0
    assert selected.files == []
    assert selected.content == ""
    assert selected.character_count == 0


def test_whitespace_query_returns_empty_context():
    results = [
        make_result(
            1,
            "auth.py",
            "login code",
            100,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="   ",
        results=results,
    )

    assert selected.file_count == 0
    assert selected.files == []
    assert selected.content == ""


# =========================================================
# EMPTY RESULTS
# =========================================================


def test_empty_results_return_empty_context():
    selector = RelevantContextSelector()

    selected = selector.select(
        query="authentication",
        results=[],
    )

    assert selected.file_count == 0
    assert selected.files == []
    assert selected.content == ""
    assert selected.character_count == 0


# =========================================================
# CONTEXT HEADER
# =========================================================


def test_context_contains_repository_header():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="login",
        results=results,
    )

    assert (
        "RELEVANT REPOSITORY CONTEXT"
        in selected.content
    )


def test_context_contains_user_query():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="authentication login",
        results=results,
    )

    assert (
        "USER QUERY: authentication login"
        in selected.content
    )


# =========================================================
# FILE PATH
# =========================================================


def test_context_contains_file_path():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
            path="services/auth.py",
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="login",
        results=results,
    )

    assert (
        "--- FILE: services/auth.py ---"
        in selected.content
    )


# =========================================================
# LANGUAGE
# =========================================================


def test_context_contains_language():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
            language="python",
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="login",
        results=results,
    )

    assert "LANGUAGE: python" in selected.content
    assert "```python" in selected.content


def test_context_supports_file_without_language():
    results = [
        make_result(
            1,
            "unknown.xyz",
            "some content",
            100,
            language=None,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="content",
        results=results,
    )

    assert "some content" in selected.content
    assert "```" in selected.content


# =========================================================
# CHARACTER COUNT
# =========================================================


def test_character_count_matches_content_length():
    results = [
        make_result(
            1,
            "auth.py",
            "def login(): pass",
            100,
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="login",
        results=results,
    )

    assert (
        selected.character_count
        == len(selected.content)
    )


# =========================================================
# TIE BREAKING
# =========================================================


def test_equal_scores_are_sorted_by_path():
    results = [
        make_result(
            1,
            "z.py",
            "code",
            50,
            path="z.py",
        ),
        make_result(
            2,
            "a.py",
            "code",
            50,
            path="a.py",
        ),
    ]

    selector = RelevantContextSelector()

    selected = selector.select(
        query="code",
        results=results,
    )

    assert [
        file.path
        for file in selected.files
    ] == [
        "a.py",
        "z.py",
    ]


# =========================================================
# INVALID CONFIGURATION
# =========================================================


def test_zero_max_chars_is_rejected():
    try:
        RelevantContextSelector(
            max_chars=0,
        )
        assert False
    except ValueError:
        assert True


def test_zero_max_files_is_rejected():
    try:
        RelevantContextSelector(
            max_files=0,
        )
        assert False
    except ValueError:
        assert True