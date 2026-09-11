from dataclasses import dataclass
import re


@dataclass
class RankedContextItem:
    file_id: int
    name: str
    path: str
    content: str
    language: str | None
    score: float


class ContextRanker:
    """
    Ranks repository search results by relevance
    to the user's query.
    """

    FILENAME_EXACT_SCORE = 50.0
    FILENAME_TERM_SCORE = 15.0
    PATH_TERM_SCORE = 7.0
    CONTENT_TERM_SCORE = 1.0
    CONTENT_TERM_MAX_SCORE = 10.0

    def rank(
        self,
        results: list,
        query: str,
    ) -> list[RankedContextItem]:

        if not results:
            return []

        query_terms = self._tokenize(query)

        ranked_items: list[RankedContextItem] = []

        for result in results:
            score = self._calculate_score(
                result=result,
                query_terms=query_terms,
                query=query,
            )

            ranked_items.append(
                RankedContextItem(
                    file_id=result.file_id,
                    name=result.name,
                    path=result.path,
                    content=result.content,
                    language=result.language,
                    score=score,
                )
            )

        ranked_items.sort(
            key=lambda item: (
                -item.score,
                item.path.lower(),
            )
        )

        return ranked_items

    def _calculate_score(
        self,
        result,
        query_terms: list[str],
        query: str,
    ) -> float:

        if not query_terms:
            return 0.0

        name = (result.name or "").lower()
        path = (result.path or "").lower()
        content = (result.content or "").lower()

        normalized_query = query.lower().strip()

        score = 0.0

        # Exact filename match gets the strongest signal.
        if name == normalized_query:
            score += self.FILENAME_EXACT_SCORE

        for term in query_terms:

            # Filename relevance.
            if self._contains_term(name, term):
                score += self.FILENAME_TERM_SCORE

            # Path relevance.
            if self._contains_term(path, term):
                score += self.PATH_TERM_SCORE

            # Content relevance.
            occurrences = self._count_term_occurrences(
                content,
                term,
            )

            score += min(
                occurrences * self.CONTENT_TERM_SCORE,
                self.CONTENT_TERM_MAX_SCORE,
            )

        return score

    def _contains_term(
        self,
        text: str,
        term: str,
    ) -> bool:

        if not text or not term:
            return False

        pattern = rf"\b{re.escape(term)}\b"

        return re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ) is not None

    def _count_term_occurrences(
        self,
        text: str,
        term: str,
    ) -> int:

        if not text or not term:
            return 0

        pattern = rf"\b{re.escape(term)}\b"

        return len(
            re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )
        )

    def _tokenize(
        self,
        query: str,
    ) -> list[str]:

        if not query:
            return []

        words = re.findall(
            r"[A-Za-z0-9_]+",
            query.lower(),
        )

        terms = [
            word
            for word in words
            if len(word) >= 2
        ]

        return list(
            dict.fromkeys(terms)
        )