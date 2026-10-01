import re
from dataclasses import replace
from typing import Protocol

from App.service.AI.context.repository_relevance import (
    RepositoryRelevanceResult,
    RepositoryRelevanceService,
)


class RepositoryContextProvider(Protocol):
    def build_context(
        self,
        query: str,
        search_limit: int | None = None,
    ) -> RepositoryRelevanceResult:
        ...


class RepositoryQuestionService:
    STOP_WORDS = {
        "a",
        "about",
        "and",
        "does",
        "explain",
        "file",
        "for",
        "from",
        "handle",
        "handled",
        "how",
        "in",
        "is",
        "me",
        "of",
        "the",
        "to",
        "where",
        "which",
        "work",
        "works",
    }

    def __init__(
        self,
        db,
        project_id: int,
        max_chars: int = 30000,
        max_files: int = 8,
        context_provider: RepositoryContextProvider | None = None,
    ):
        self.relevance_service = context_provider or RepositoryRelevanceService(
            db=db,
            project_id=project_id,
            max_chars=max_chars,
            max_files=max_files,
        )

    def build_context(
        self,
        question: str,
        search_limit: int | None = None,
    ) -> RepositoryRelevanceResult:
        retrieval_query = self._normalize_question(question)
        if not retrieval_query:
            retrieval_query = question

        result = self.relevance_service.build_context(
            query=retrieval_query,
            search_limit=search_limit,
        )

        if retrieval_query == question:
            return result

        content = result.content.replace(
            f"USER QUERY: {retrieval_query}",
            f"USER QUERY: {question}",
            1,
        )
        return replace(
            result,
            query=question,
            content=content,
            character_count=len(content),
        )

    @classmethod
    def _normalize_question(cls, question: str) -> str:
        terms = re.findall(r"[A-Za-z0-9_]+", question.lower())
        normalized_terms = []

        for term in terms:
            if term in cls.STOP_WORDS or len(term) < 2:
                continue

            if term.endswith("ation") and len(term) > 7:
                term = term[:-3]
            elif term.endswith("ies") and len(term) > 4:
                term = term[:-3] + "y"
            elif term.endswith("ed") and len(term) > 4:
                term = term[:-1]
            elif term.endswith("s") and len(term) > 3:
                term = term[:-1]

            if term and term not in normalized_terms:
                normalized_terms.append(term)

        return " ".join(normalized_terms)