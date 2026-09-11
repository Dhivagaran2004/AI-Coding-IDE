from types import SimpleNamespace
from dataclasses import dataclass

from App.service.AI.ranking.context_ranker import (
    ContextRanker,
)

@dataclass
class FakeSearchResult:
    file_id: int
    name: str
    path: str
    content: str
    language: str | None = None
    score: float = 0.0


def create_result(
    file_id,
    name,
    path,
    content,
    language="python",
    score=0.0,
):
    return SimpleNamespace(
        file_id=file_id,
        name=name,
        path=path,
        content=content,
        language=language,
        score=score,
    )


def test_filename_match_gets_highest_priority():
    ranker = ContextRanker()

    results = [
        create_result(
            file_id=1,
            name="database.py",
            path="database.py",
            content="database connection",
        ),
        create_result(
            file_id=2,
            name="authentication.py",
            path="auth/authentication.py",
            content="login authentication",
        ),
    ]

    ranked = ranker.rank(
        results,
        "authentication",
    )

    assert ranked[0].file_id == 2

    assert ranked[0].score > ranked[1].score


def test_path_match_affects_ranking():
    ranker = ContextRanker()

    results = [
        create_result(
            file_id=1,
            name="service.py",
            path="database/service.py",
            content="connection handling",
        ),
        create_result(
            file_id=2,
            name="service.py",
            path="auth/service.py",
            content="authentication handling",
        ),
    ]

    ranked = ranker.rank(
        results,
        "authentication",
    )

    assert ranked[0].file_id == 2


def test_content_match_affects_ranking():
    ranker = ContextRanker()

    results = [
        create_result(
            file_id=1,
            name="service.py",
            path="service.py",
            content="authentication",
        ),
        create_result(
            file_id=2,
            name="other.py",
            path="other.py",
            content=(
                "authentication "
                "authentication "
                "authentication"
            ),
        ),
    ]

    ranked = ranker.rank(
        results,
        "authentication",
    )

    assert ranked[0].file_id == 2


def test_results_are_sorted_by_score():
    ranker = ContextRanker()

    results = [
        create_result(
            file_id=1,
            name="one.py",
            path="one.py",
            content="",
        ),
        create_result(
            file_id=2,
            name="authentication.py",
            path="authentication.py",
            content="authentication",
        ),
        create_result(
            file_id=3,
            name="auth.py",
            path="auth.py",
            content="authentication",
        ),
    ]

    ranked = ranker.rank(
        results,
        "authentication",
    )

    assert ranked[0].file_id == 2


def test_empty_results_return_empty_list():
    ranker = ContextRanker()

    ranked = ranker.rank(
        [],
        "authentication",
    )

    assert ranked == []


def test_empty_query_returns_zero_scores():
    ranker = ContextRanker()

    results = [
        create_result(
            file_id=1,
            name="main.py",
            path="main.py",
            content="hello",
        ),
    ]

    ranked = ranker.rank(
        results,
        "",
    )

    assert len(ranked) == 1
    assert ranked[0].score == 0.0


def test_duplicate_query_terms_are_removed():
    ranker = ContextRanker()

    terms = ranker._tokenize(
        "authentication authentication login"
    )

    assert terms == [
        "authentication",
        "login",
    ]

def test_exact_filename_match_gets_highest_score():
    results = [
        FakeSearchResult(
            file_id=1,
            name="database.py",
            path="app/database.py",
            content="authentication database",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="authentication",
            path="app/authentication",
            content="authentication logic",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="authentication",
    )

    assert ranked[0].name == "authentication"


def test_filename_match_ranks_above_content_only_match():
    results = [
        FakeSearchResult(
            file_id=1,
            name="database.py",
            path="app/database.py",
            content="authentication",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="authentication.py",
            path="app/authentication.py",
            content="login logic",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="authentication",
    )

    assert ranked[0].name == "authentication.py"


def test_whole_word_matching_avoids_partial_words():
    results = [
        FakeSearchResult(
            file_id=1,
            name="author.py",
            path="app/author.py",
            content="author information",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="auth.py",
            path="app/auth.py",
            content="auth logic",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="auth",
    )

    assert ranked[0].name == "auth.py"


def test_multiple_query_terms_are_ranked():
    results = [
        FakeSearchResult(
            file_id=1,
            name="database.py",
            path="app/database.py",
            content="database connection",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="auth.py",
            path="app/auth.py",
            content="authentication login",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="authentication login",
    )

    assert ranked[0].name == "auth.py"


def test_ranking_is_case_insensitive():
    results = [
        FakeSearchResult(
            file_id=1,
            name="Auth.py",
            path="App/Auth.py",
            content="Authentication Login",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="database.py",
            path="App/database.py",
            content="database",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="AUTHENTICATION",
    )

    assert ranked[0].name == "Auth.py"


def test_empty_query_produces_stable_order():
    results = [
        FakeSearchResult(
            file_id=1,
            name="z.py",
            path="z.py",
            content="",
            language="python",
        ),
        FakeSearchResult(
            file_id=2,
            name="a.py",
            path="a.py",
            content="",
            language="python",
        ),
    ]

    ranker = ContextRanker()

    ranked = ranker.rank(
        results=results,
        query="",
    )

    assert ranked[0].path == "a.py"
    assert ranked[1].path == "z.py"