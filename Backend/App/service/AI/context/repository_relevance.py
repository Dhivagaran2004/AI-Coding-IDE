import logging
from dataclasses import dataclass, replace

from App.service.AI.context.relevant_context import (
    RelevantContextSelector,
    SelectedContext,
)
from App.service.AI.index.indexed_context_service import (
    IndexedContextService,
)
from App.service.AI.ranking.context_ranker import (
    ContextRanker,
)
from App.service.AI.search.file_search import (
    FileSearchResult,
    FileSearchService,
)


logger = logging.getLogger(__name__)


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
        self.db = db
        self.project_id = project_id
        self.max_chars = max_chars
        self.max_files = max_files

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

        if selected_context.files:
            indexed_by_id = {}
            try:
                indexed_context = IndexedContextService(
                    db=self.db,
                    project_id=self.project_id,
                    max_chars=self.max_chars,
                    max_files=self.max_files,
                ).build_context(
                    file_ids=[
                        result.file_id
                        for result in selected_context.files
                    ]
                )
                indexed_by_id = {
                    indexed_file.file_id: indexed_file.content
                    for indexed_file in indexed_context.files
                }
            except Exception as exc:
                logger.warning(
                    "Indexed repository context unavailable for project %s: %s",
                    self.project_id,
                    exc,
                )

            if indexed_by_id:
                selected_ids = {
                    result.file_id
                    for result in selected_context.files
                }
                selected_results = [
                    replace(
                        result,
                        content=indexed_by_id.get(
                            result.file_id,
                            result.content,
                        ),
                    )
                    for result in ranked_results
                    if result.file_id in selected_ids
                ]
                selected_context = self.selector.select(
                    query,
                    selected_results,
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