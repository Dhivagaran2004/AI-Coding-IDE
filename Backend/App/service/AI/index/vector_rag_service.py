from dataclasses import dataclass
import hashlib
import math
from typing import cast

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile
from App.models.repository_chunk import RepositoryChunk
from App.service.AI.embedding_service import (
    EmbeddingService,
    create_embedding_service,
)
from App.service.AI.index.code_chunker import CodeChunk, CodeChunker


@dataclass
class RetrievedChunk:
    project_id: int
    file_id: int
    file_path: str
    language: str | None
    start_line: int
    end_line: int
    content: str
    score: float
    metadata: dict[str, object]


@dataclass
class VectorSearchResult:
    available: bool
    chunks: list[RetrievedChunk]
    content: str


class VectorStore:
    """Portable project-scoped vector persistence using SQL JSON values."""

    def __init__(self, db: Session):
        self.db = db

    def get_project_chunks(
        self,
        project_id: int,
    ) -> list[RepositoryChunk]:
        return (
            self.db.query(RepositoryChunk)
            .filter(RepositoryChunk.project_id == project_id)
            .all()
        )

    def delete_file_chunks(
        self,
        project_id: int,
        file_id: int,
    ) -> None:
        (
            self.db.query(RepositoryChunk)
            .filter(
                RepositoryChunk.project_id == project_id,
                RepositoryChunk.file_id == file_id,
            )
            .delete(synchronize_session="fetch")
        )
        self.db.commit()

    def delete_files_chunks(
        self,
        project_id: int,
        file_ids: list[int],
    ) -> None:
        if not file_ids:
            return
        (
            self.db.query(RepositoryChunk)
            .filter(
                RepositoryChunk.project_id == project_id,
                RepositoryChunk.file_id.in_(file_ids),
            )
            .delete(synchronize_session="fetch")
        )
        self.db.commit()

    def store_file_chunks(
        self,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
        content_hash: str,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Each repository chunk must have one embedding.")
        for chunk, embedding in zip(chunks, embeddings):
            self.db.add(
                RepositoryChunk(
                    project_id=chunk.project_id,
                    file_id=chunk.file_id,
                    file_path=chunk.file_path,
                    language=chunk.language,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    content_hash=content_hash,
                    embedding=embedding,
                    chunk_metadata={
                        "project_id": chunk.project_id,
                        "file_id": chunk.file_id,
                        "file_path": chunk.file_path,
                        "language": chunk.language,
                    },
                )
            )
        self.db.commit()


class VectorRAGService:
    MAX_TOP_K = 10

    def __init__(
        self,
        db: Session,
        project_id: int,
        embedding_service: EmbeddingService | None = None,
        vector_store: VectorStore | None = None,
        min_similarity: float = 0.2,
        max_context_chars: int = 20000,
    ):
        self.db = db
        self.project_id = project_id
        self.embedding_service = embedding_service or create_embedding_service()
        self.vector_store = vector_store or VectorStore(db)
        self.min_similarity = min_similarity
        self.max_context_chars = max_context_chars

    def index_file(
        self,
        file: ProjectFile,
        file_path: str | None = None,
    ) -> int:
        file_id = cast(int, file.id)
        self.vector_store.delete_file_chunks(self.project_id, file_id)
        path = file_path or self._get_file_path(file)
        chunks = CodeChunker().chunk_file(
            project_id=self.project_id,
            file_id=file_id,
            file_path=path,
            language=cast(str | None, file.language),
            content=cast(str | None, file.content),
        )
        if not chunks or not self.embedding_service.available:
            return 0

        embeddings = self.embedding_service.embed_texts(
            [chunk.content for chunk in chunks]
        )
        content_hash = hashlib.sha256(
            (file.content or "").encode("utf-8")
        ).hexdigest()
        self.vector_store.store_file_chunks(
            chunks,
            embeddings,
            content_hash,
        )
        return len(chunks)

    def ensure_file_indexed(
        self,
        file: ProjectFile,
        file_path: str | None = None,
    ) -> int:
        if not self.embedding_service.available:
            return 0

        content_hash = hashlib.sha256(
            (cast(str | None, file.content) or "").encode("utf-8")
        ).hexdigest()
        file_id = cast(int, file.id)
        existing = [
            row for row in self.vector_store.get_project_chunks(self.project_id)
            if cast(int, row.file_id) == file_id
        ]
        current_path = file_path or self._get_file_path(file)
        if existing and all(
            cast(str, row.content_hash) == content_hash
            and cast(str, row.file_path) == current_path
            for row in existing
        ):
            return 0
        return self.index_file(file, file_path=current_path)

    def delete_file(self, file_id: int) -> None:
        self.vector_store.delete_file_chunks(self.project_id, file_id)

    def delete_files(self, file_ids: list[int]) -> None:
        self.vector_store.delete_files_chunks(self.project_id, file_ids)

    def search_project_context(
        self,
        project_id: int,
        query: str,
        top_k: int = 5,
    ) -> VectorSearchResult:
        if project_id != self.project_id:
            raise ValueError("Vector search project scope mismatch.")
        if not query.strip():
            return VectorSearchResult(True, [], "")
        if not self.embedding_service.available:
            return VectorSearchResult(False, [], "")

        rows = self.vector_store.get_project_chunks(project_id)
        if not rows:
            return VectorSearchResult(True, [], "")

        current_files = self._get_current_files(rows, project_id)
        current_rows = [
            row for row in rows
            if cast(int, row.file_id) in current_files
            and cast(str, row.content_hash)
            == current_files[cast(int, row.file_id)]
            and CodeChunker.is_indexable_path(cast(str, row.file_path))
            and not CodeChunker.contains_sensitive_content(
                cast(str, row.content)
            )
        ]
        if not current_rows:
            return VectorSearchResult(True, [], "")

        query_embedding = self.embedding_service.embed_texts([query])[0]
        scored: list[RetrievedChunk] = []
        for row in current_rows:
            score = self.cosine_similarity(
                query_embedding,
                cast(list[float], row.embedding),
            )
            if score < self.min_similarity:
                continue
            scored.append(
                RetrievedChunk(
                    project_id=cast(int, row.project_id),
                    file_id=cast(int, row.file_id),
                    file_path=cast(str, row.file_path),
                    language=cast(str | None, row.language),
                    start_line=cast(int, row.start_line),
                    end_line=cast(int, row.end_line),
                    content=cast(str, row.content),
                    score=score,
                    metadata=cast(dict[str, object], row.chunk_metadata or {}),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        selected = scored[:max(1, min(top_k, self.MAX_TOP_K))]
        sections: list[str] = []
        total_chars = 0
        for item in selected:
            section = (
                f"--- FILE: {item.file_path} "
                f"(lines {item.start_line}-{item.end_line}) ---\n"
                f"LANGUAGE: {item.language or ''}\n"
                f"```{item.language or ''}\n{item.content}\n```"
            )
            if total_chars + len(section) > self.max_context_chars:
                break
            sections.append(section)
            total_chars += len(section)

        return VectorSearchResult(
            available=True,
            chunks=selected[:len(sections)],
            content="\n\n".join(sections),
        )

    def _get_current_files(
        self,
        rows: list[RepositoryChunk],
        project_id: int,
    ) -> dict[int, str]:
        file_ids = {cast(int, row.file_id) for row in rows}
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.id.in_(file_ids),
            )
            .all()
        )
        return {
            cast(int, file.id): hashlib.sha256(
                (cast(str | None, file.content) or "").encode("utf-8")
            ).hexdigest()
            for file in files
        }

    def _get_file_path(self, file: ProjectFile) -> str:
        parts = [cast(str, file.name)]
        parent_id = cast(int | None, file.parent_id)
        file_id = cast(int, file.id)
        visited = {file_id}
        while parent_id is not None and parent_id not in visited:
            visited.add(parent_id)
            parent = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.id == parent_id,
                    ProjectFile.project_id == self.project_id,
                )
                .first()
            )
            if parent is None:
                break
            parts.append(cast(str, parent.name))
            parent_id = cast(int | None, parent.parent_id)
        return "/".join(reversed(parts))

    @staticmethod
    def cosine_similarity(
        left: list[float],
        right: list[float],
    ) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot_product = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return dot_product / (left_norm * right_norm)