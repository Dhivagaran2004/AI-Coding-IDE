from __future__ import annotations

from dataclasses import dataclass

from App.service.AI.search.file_search import (
    FileSearchResult,
)


@dataclass
class SelectedContext:
    """
    Context selected from repository search results.
    """

    query: str
    content: str
    files: list[FileSearchResult]
    file_count: int
    character_count: int


class RelevantContextSelector:
    """
    Selects the most relevant repository files
    for an AI request.

    This layer sits between:

        FileSearchService
                ↓
        RelevantContextSelector
                ↓
        AI model
    """

    DEFAULT_MAX_CHARS = 30000
    DEFAULT_MAX_FILES = 8

    def __init__(
        self,
        max_chars: int | None = None,
        max_files: int | None = None,
    ):
        self.max_chars = (
            max_chars
            if max_chars is not None
            else self.DEFAULT_MAX_CHARS
        )

        self.max_files = (
            max_files
            if max_files is not None
            else self.DEFAULT_MAX_FILES
        )

        if self.max_chars <= 0:
            raise ValueError(
                "max_chars must be greater than zero."
            )

        if self.max_files <= 0:
            raise ValueError(
                "max_files must be greater than zero."
            )

    # =========================================================
    # PUBLIC API
    # =========================================================

    def select(
        self,
        query: str,
        results: list[FileSearchResult],
    ) -> SelectedContext:
        """
        Select the most relevant files from search results.
        """

        query = query.strip()

        if not query or not results:
            return SelectedContext(
                query=query,
                content="",
                files=[],
                file_count=0,
                character_count=0,
            )

        ranked_results = sorted(
            results,
            key=lambda result: (
                -result.score,
                result.path.lower(),
            ),
        )

        selected_files: list[
            FileSearchResult
        ] = []

        current_characters = 0

        for result in ranked_results:

            if len(selected_files) >= self.max_files:
                break

            section = self._format_file(
                result
            )

            section_size = len(section)

            if (
                current_characters
                + section_size
                > self.max_chars
            ):
                continue

            selected_files.append(result)

            current_characters += section_size

        content = self._build_context(
            query=query,
            files=selected_files,
        )

        return SelectedContext(
            query=query,
            content=content,
            files=selected_files,
            file_count=len(selected_files),
            character_count=len(content),
        )

    # =========================================================
    # FILE FORMATTING
    # =========================================================

    def _format_file(
        self,
        result: FileSearchResult,
    ) -> str:
        """
        Format one search result for the AI.
        """

        language = result.language or ""

        if language:
            return (
                f"--- FILE: {result.path} ---\n"
                f"LANGUAGE: {language}\n\n"
                f"```{language}\n"
                f"{result.content}\n"
                f"```\n\n"
            )

        return (
            f"--- FILE: {result.path} ---\n\n"
            f"```\n"
            f"{result.content}\n"
            f"```\n\n"
        )

    # =========================================================
    # BUILD CONTEXT
    # =========================================================

    def _build_context(
        self,
        query: str,
        files: list[FileSearchResult],
    ) -> str:
        """
        Build the final context sent to the AI.
        """

        if not files:
            return ""

        sections = [
            "RELEVANT REPOSITORY CONTEXT",
            "",
            f"USER QUERY: {query}",
            "",
            "RELEVANT FILES",
            "",
        ]

        for result in files:
            sections.append(
                self._format_file(result)
            )

        return "\n".join(
            sections
        ).strip()