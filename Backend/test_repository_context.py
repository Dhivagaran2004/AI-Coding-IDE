from types import SimpleNamespace

import pytest

from App.service.AI.context.repository_context import (
    RepositoryContextBuilder,
)


class FakeQuery:
    """
    Small fake SQLAlchemy query object.

    This allows us to test RepositoryContextBuilder
    without connecting to the real database.
    """

    def __init__(self, files):
        self.files = files
        self.filtered_files = files

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.files

    def first(self):
        if not self.files:
            return None

        return self.files[0]


class FakeDatabase:
    """
    Fake database session used by the tests.
    """

    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


def make_file(
    file_id,
    name,
    file_type="file",
    content="",
    language=None,
    parent_id=None,
):
    """
    Create a lightweight fake ProjectFile object.
    """

    return SimpleNamespace(
        id=file_id,
        project_id=1,
        parent_id=parent_id,
        name=name,
        type=file_type,
        content=content,
        language=language,
    )


# ============================================================
# TEST 1
# ============================================================


def test_builder_loads_project_files():
    files = [
        make_file(
            file_id=1,
            name="main.py",
            content="print('hello')",
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.project_id == 1
    assert result.file_count == 1
    assert "main.py" in result.content
    assert "print('hello')" in result.content


# ============================================================
# TEST 2
# ============================================================


def test_builder_excludes_folders_from_file_content():
    files = [
        make_file(
            file_id=1,
            name="app",
            file_type="folder",
        ),
        make_file(
            file_id=2,
            name="main.py",
            content="print('hello')",
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.file_count == 1
    assert "main.py" in result.content
    assert "print('hello')" in result.content


# ============================================================
# TEST 3
# ============================================================


def test_builder_creates_repository_structure():
    files = [
        make_file(
            file_id=1,
            name="app",
            file_type="folder",
        ),
        make_file(
            file_id=2,
            name="main.py",
            content="print('hello')",
            language="python",
            parent_id=1,
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert "PROJECT STRUCTURE" in result.content
    assert "app/" in result.content
    assert "main.py" in result.content


def test_build_tree_includes_empty_files_without_exposing_contents():
    files = [
        make_file(
            file_id=1,
            name="src",
            file_type="folder",
        ),
        make_file(
            file_id=2,
            name="new_module.py",
            content="",
            parent_id=1,
        ),
        make_file(
            file_id=3,
            name=".env",
            content="SECRET_KEY=hidden",
        ),
    ]

    builder = RepositoryContextBuilder(
        db=FakeDatabase(files),
        project_id=1,
    )

    tree = builder.build_tree()

    assert "src/" in tree
    assert "new_module.py" in tree
    assert ".env" not in tree
    assert "SECRET_KEY" not in tree
    assert "PROJECT FILE CONTENT" not in tree


# ============================================================
# TEST 4
# ============================================================


def test_builder_formats_language():
    files = [
        make_file(
            file_id=1,
            name="main.py",
            content="print('hello')",
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert "LANGUAGE: python" in result.content
    assert "```python" in result.content


# ============================================================
# TEST 5
# ============================================================


def test_builder_detects_language_from_extension():
    files = [
        make_file(
            file_id=1,
            name="main.py",
            content="print('hello')",
            language=None,
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert "LANGUAGE: python" in result.content
    assert "```python" in result.content


# ============================================================
# TEST 6
# ============================================================


@pytest.mark.parametrize(
    "file_name",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
    ],
)
def test_builder_excludes_environment_files(file_name):
    files = [
        make_file(
            file_id=1,
            name=file_name,
            content="SECRET_KEY=super-secret",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.file_count == 0
    assert file_name not in result.content
    assert "SECRET_KEY" not in result.content


# ============================================================
# TEST 7
# ============================================================


@pytest.mark.parametrize(
    "file_name",
    [
        "image.png",
        "photo.jpg",
        "archive.zip",
        "program.exe",
        "module.pyc",
        "document.pdf",
    ],
)
def test_builder_excludes_binary_files(file_name):
    files = [
        make_file(
            file_id=1,
            name=file_name,
            content="binary data",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.file_count == 0
    assert file_name not in result.content


# ============================================================
# TEST 8
# ============================================================


def test_builder_excludes_empty_files():
    files = [
        make_file(
            file_id=1,
            name="empty.py",
            content="",
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.file_count == 0
    assert "empty.py" not in result.content


# ============================================================
# TEST 9
# ============================================================


def test_builder_respects_character_limit():
    large_content = "x" * 10000

    files = [
        make_file(
            file_id=1,
            name="large.py",
            content=large_content,
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
        max_chars=1000,
    )

    result = builder.build()

    assert len(result.content) > 0
    assert len(result.content) <= 1100
    assert "Repository context truncated" in result.content


# ============================================================
# TEST 10
# ============================================================


def test_builder_rejects_invalid_character_limit():
    files = []

    db = FakeDatabase(files)

    with pytest.raises(ValueError):
        RepositoryContextBuilder(
            db=db,
            project_id=1,
            max_chars=0,
        )


# ============================================================
# TEST 11
# ============================================================


def test_builder_handles_empty_project():
    db = FakeDatabase([])

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.project_id == 1
    assert result.file_count == 0
    assert "PROJECT STRUCTURE" in result.content
    assert "empty project" in result.content


# ============================================================
# TEST 12
# ============================================================


def test_builder_returns_character_count():
    files = [
        make_file(
            file_id=1,
            name="main.py",
            content="print('hello')",
            language="python",
        ),
    ]

    db = FakeDatabase(files)

    builder = RepositoryContextBuilder(
        db=db,
        project_id=1,
    )

    result = builder.build()

    assert result.character_count == len(
        result.content
    )