from dataclasses import dataclass

from App.service.AI.context.relevant_context import (
    RelevantContextSelector,
    SelectedContext,
)
from App.service.AI.ranking.context_ranker import (
    ContextRanker,
)
from App.service.AI.search.file_search import (
    FileSearchResult,
    FileSearchService,
)


@dataclass
class RepositoryRelevanceResult:
    query: str
    content: str
    search_results: list[FileSearchResult]
    selected_files: list
    search_result_count: int
    selected_file_count: int
    character_count: int


class RepositoryRelevanceService:
    """
    Searches the repository, ranks the search results,
    and selects the most relevant files for AI context.
    """

    def __init__(
        self,
        db,
        project_id: int,
        max_chars: int = 30000,
        max_files: int = 8,
    ):
        self.search_service = FileSearchService(
            db=db,
            project_id=project_id,
        )

        self.ranker = ContextRanker()

        self.selector = RelevantContextSelector(
            max_chars=max_chars,
            max_files=max_files,
        )

    def build_context(
        self,
        query: str,
        search_limit: int | None = None,
    ) -> RepositoryRelevanceResult:

        if not query.strip():
            return RepositoryRelevanceResult(
                query=query,
                content="",
                search_results=[],
                selected_files=[],
                search_result_count=0,
                selected_file_count=0,
                character_count=0,
            )

        if search_limit is None:
            search_limit = 20

        if search_limit < 1:
            raise ValueError(
                "search_limit must be greater than 0."
            )

        search_results = self.search_service.search(
            query=query,
            limit=search_limit,
        )

        ranked_results = self.ranker.rank(
            results=search_results,
            query=query,
        )

        selected_context: SelectedContext = (
            self.selector.select(
                query,
                ranked_results,
            )
        )

        return RepositoryRelevanceResult(
            query=query,
            content=selected_context.content,
            search_results=search_results,
            selected_files=selected_context.files,
            search_result_count=len(search_results),
            selected_file_count=selected_context.file_count,
            character_count=selected_context.character_count,
        )