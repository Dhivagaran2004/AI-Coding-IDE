import re
from datetime import datetime
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from App.database.database import Base
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.repository_chunk import RepositoryChunk
from App.models.user import User
from App.service.AI.embedding_service import EmbeddingService
from App.service.AI.embedding_service import EmbeddingProvider
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)
from App.service.AI.index.vector_rag_service import VectorRAGService


class KeywordEmbeddingProvider:
    groups = (
        {"auth", "authentication", "login", "jwt", "token"},
        {"billing", "invoice", "payment"},
        {"database", "query", "sql"},
    )

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            terms = set(re.findall(r"[a-z]+", text.lower()))
            vectors.append([
                float(bool(terms & group))
                for group in self.groups
            ])
        return vectors


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(
            id=1,
            name="Owner One",
            email="one@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
        User(
            id=2,
            name="Owner Two",
            email="two@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
    ])
    session.flush()
    session.add_all([
        Project(id=1, user_id=1, name="Project One", language="python"),
        Project(id=2, user_id=2, name="Project Two", language="python"),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def add_file(
    db: Session,
    project_id: int,
    name: str,
    content: str,
    language: str | None = "python",
) -> ProjectFile:
    file = ProjectFile(
        project_id=project_id,
        name=name,
        type="file",
        content=content,
        language=language,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(file)
    db.commit()
    return file


def create_vector_service(
    db: Session,
    project_id: int,
    provider: EmbeddingProvider | None = None,
) -> VectorRAGService:
    embedding_service = EmbeddingService(
        provider or KeywordEmbeddingProvider()
    )
    return VectorRAGService(
        db,
        project_id,
        embedding_service=embedding_service,
        min_similarity=0.25,
    )


def test_chunker_preserves_path_language_and_line_ranges() -> None:
    chunker = CodeChunker(max_lines=4, overlap_lines=1, max_chars=1000)
    content = "\n".join(f"line_{line}" for line in range(1, 10))

    chunks = chunker.chunk_file(
        project_id=7,
        file_id=12,
        file_path="src/auth.py",
        language="python",
        content=content,
    )

    assert len(chunks) == 3
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 4
    assert chunks[1].start_line == 4
    assert chunks[-1].end_line == 9
    assert all(chunk.project_id == 7 for chunk in chunks)
    assert all(chunk.file_id == 12 for chunk in chunks)
    assert all(chunk.file_path == "src/auth.py" for chunk in chunks)
    assert all(chunk.language == "python" for chunk in chunks)


@pytest.mark.parametrize(
    "file_path,content",
    [
        (".env", "API_KEY=value"),
        ("config/.env.production", "SECRET=value"),
        ("src/service_credentials.py", "password = 'x'"),
        ("src/config.py", 'API_KEY = "ghp_12345678901234567890"'),
        ("src/key.py", "-----BEGIN PRIVATE KEY-----"),
        ("Frontend/package-lock.json", '{"lockfileVersion": 3}'),
        ("node_modules/pkg/index.js", "module.exports = {}"),
        (".next/server/app.js", "generated bundle"),
        (".pytest_cache/v/cache/nodeids", "generated cache"),
        ("assets/logo.png", "not really text"),
        ("src/data.bin", "\x00binary"),
        ("data/local.sqlite3", "database"),
        ("assets/font.woff2", "font binary"),
    ],
)
def test_chunker_filters_secrets_binaries_and_generated_paths(
    file_path: str,
    content: str,
) -> None:
    chunks = CodeChunker().chunk_file(
        1,
        2,
        file_path,
        "text",
        content,
    )

    assert chunks == []


def test_index_creates_embeddings_and_stores_vector_chunks(
    db_session: Session,
) -> None:
    provider = KeywordEmbeddingProvider()
    service = create_vector_service(db_session, 1, provider)
    file = add_file(
        db_session,
        1,
        "auth.py",
        "def login(token):\n    return authenticate(token)\n",
    )

    count = service.index_file(file)
    rows = db_session.query(RepositoryChunk).filter(
        RepositoryChunk.project_id == 1
    ).all()

    assert count == 1
    assert len(provider.calls) == 1
    assert len(rows) == 1
    assert bool(getattr(rows[0], "embedding"))
    assert getattr(rows[0], "file_path") == "auth.py"
    assert getattr(rows[0], "start_line") == 1
    assert getattr(rows[0], "end_line") == 2
    assert getattr(rows[0], "chunk_metadata")["file_id"] == getattr(file, "id")


def test_repository_index_update_rebuilds_vectors_and_removes_stale_content(
    db_session: Session,
) -> None:
    vector_service = create_vector_service(db_session, 1)
    file = add_file(
        db_session,
        1,
        "auth.py",
        "def login(token): return authenticate(token)",
    )
    index_service = RepositoryIndexService(
        db_session,
        1,
        vector_index_service=vector_service,
    )
    index_service.create_or_update_index(file)
    assert db_session.query(RepositoryChunk).count() == 1

    setattr(file, "content", "def invoice_total(items): return sum(items)")
    db_session.commit()
    index_service.create_or_update_index(file)

    rows = db_session.query(RepositoryChunk).all()
    assert len(rows) == 1
    assert "invoice_total" in getattr(rows[0], "content")
    assert "authenticate" not in getattr(rows[0], "content")


def test_full_reindex_backfills_vectors_for_existing_ready_files(
    db_session: Session,
) -> None:
    file = add_file(
        db_session,
        1,
        "auth.py",
        "def login(token): return authenticate(token)",
    )

    class NoVectorIndexer:
        def index_file(self, file: ProjectFile) -> int:
            return 0

    RepositoryIndexService(
        db_session,
        1,
        vector_index_service=NoVectorIndexer(),
    ).create_or_update_index(file)
    vector_service = create_vector_service(db_session, 1)
    result = RepositoryIndexService(
        db_session,
        1,
        vector_index_service=vector_service,
    ).index_project()

    assert result["skipped_count"] == 1
    assert result["vector_indexed_count"] == 1
    assert db_session.query(RepositoryChunk).count() == 1


def test_similarity_search_is_project_scoped_and_top_k_is_bounded(
    db_session: Session,
) -> None:
    project_one_service = create_vector_service(db_session, 1)
    project_two_service = create_vector_service(db_session, 2)
    for number in range(12):
        file = add_file(
            db_session,
            1,
            f"auth_{number}.py",
            "authentication login jwt token",
        )
        project_one_service.index_file(file)
    other_project_file = add_file(
        db_session,
        2,
        "other.py",
        "authentication login jwt token",
    )
    project_two_service.index_file(other_project_file)

    result = project_one_service.search_project_context(
        1,
        "Where is authentication login implemented?",
        top_k=100,
    )

    assert len(result.chunks) == 10
    assert all(chunk.project_id == 1 for chunk in result.chunks)
    assert "other.py" not in result.content


def test_file_deletion_removes_vectors(db_session: Session) -> None:
    service = create_vector_service(db_session, 1)
    file = add_file(db_session, 1, "auth.py", "login jwt authentication")
    service.index_file(file)
    assert db_session.query(RepositoryChunk).count() == 1

    service.delete_file(getattr(file, "id"))

    assert db_session.query(RepositoryChunk).count() == 0


def test_unchanged_file_path_change_refreshes_vector_metadata(
    db_session: Session,
) -> None:
    service = create_vector_service(db_session, 1)
    file = add_file(db_session, 1, "auth.py", "login jwt authentication")
    service.index_file(file)
    setattr(file, "name", "auth_service.py")
    db_session.commit()

    count = service.ensure_file_indexed(file)
    rows = db_session.query(RepositoryChunk).all()

    assert count == 1
    assert len(rows) == 1
    assert getattr(rows[0], "file_path") == "auth_service.py"


def test_stale_file_vectors_are_not_retrieved(db_session: Session) -> None:
    service = create_vector_service(db_session, 1)
    file = add_file(db_session, 1, "auth.py", "login jwt authentication")
    service.index_file(file)
    setattr(file, "content", "unrelated source with no matching concepts")
    db_session.commit()

    result = service.search_project_context(
        1,
        "authentication login",
    )

    assert result.chunks == []
    assert result.content == ""


def test_empty_and_irrelevant_retrieval_return_no_chunks(
    db_session: Session,
) -> None:
    service = create_vector_service(db_session, 1)
    file = add_file(db_session, 1, "auth.py", "login jwt authentication")
    service.index_file(file)

    empty_result = service.search_project_context(1, "   ")
    irrelevant_result = service.search_project_context(
        1,
        "astronomy galaxies telescope",
    )

    assert empty_result.available is True
    assert empty_result.chunks == []
    assert irrelevant_result.chunks == []


def test_retrieval_falls_back_when_embeddings_are_unavailable(
    db_session: Session,
) -> None:
    indexed = create_vector_service(db_session, 1)
    file = add_file(db_session, 1, "auth.py", "login jwt authentication")
    indexed.index_file(file)
    unavailable = VectorRAGService(
        db_session,
        1,
        embedding_service=EmbeddingService(None),
    )

    result = unavailable.search_project_context(
        1,
        "authentication login",
    )

    assert result.available is False
    assert result.content == ""


def test_unavailable_embeddings_are_observable_without_stored_vectors(
    db_session: Session,
) -> None:
    service = VectorRAGService(
        db_session,
        1,
        embedding_service=EmbeddingService(None),
    )

    result = service.search_project_context(1, "authentication")

    assert result.available is False
    assert result.chunks == []
    assert result.content == ""