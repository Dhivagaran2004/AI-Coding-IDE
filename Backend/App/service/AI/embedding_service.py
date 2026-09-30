import json
import math
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from App.config import LLM_API_KEY


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class HuggingFaceEmbeddingProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "BAAI/bge-small-en-v1.5",
        timeout: int = 45,
    ):
        if not api_key:
            raise ValueError("A Hugging Face API key is required.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.endpoint = (
            "https://router.huggingface.co/hf-inference/models/"
            f"{quote(model, safe='/')}"
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch_start in range(0, len(texts), 16):
            batch = texts[batch_start:batch_start + 16]
            payload = json.dumps({
                "inputs": batch[0] if len(batch) == 1 else batch,
            }).encode("utf-8")
            request = Request(
                self.endpoint,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError) as error:
                raise RuntimeError(
                    f"Hugging Face embedding request failed: {error}"
                ) from error

            if isinstance(result, dict):
                raise RuntimeError(
                    result.get("error", "Invalid embedding response.")
                )
            if (
                len(batch) == 1
                and isinstance(result, list)
                and result
                and all(isinstance(item, (int, float)) for item in result)
            ):
                batch_result = [result]
            elif len(batch) == 1 and result and isinstance(result[0], list):
                batch_result = [result]
            elif isinstance(result, list) and len(result) == len(batch):
                batch_result = result
            else:
                raise RuntimeError("Embedding response size did not match input.")

            vectors.extend(self._mean_pool(item) for item in batch_result)

        if any(not vector or any(not math.isfinite(value) for value in vector)
               for vector in vectors):
            raise RuntimeError("Embedding response contained invalid values.")
        return vectors

    @classmethod
    def _mean_pool(cls, value) -> list[float]:
        if not isinstance(value, list) or not value:
            raise RuntimeError("Embedding response contained an empty vector.")
        if all(isinstance(item, (int, float)) for item in value):
            return [float(item) for item in value]

        vectors = [
            cls._mean_pool(item)
            for item in value
            if isinstance(item, list)
        ]
        if not vectors or any(len(vector) != len(vectors[0]) for vector in vectors):
            raise RuntimeError("Embedding response dimensions were inconsistent.")
        return [
            sum(vector[index] for vector in vectors) / len(vectors)
            for index in range(len(vectors[0]))
        ]


class EmbeddingService:
    def __init__(self, provider: EmbeddingProvider | None):
        self.provider = provider

    @property
    def available(self) -> bool:
        return self.provider is not None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.provider is None:
            raise RuntimeError("Embedding provider is not configured.")
        vectors = self.provider.embed_texts(texts)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned an invalid result count.")
        return vectors


def create_embedding_service() -> EmbeddingService:
    api_key = os.getenv("EMBEDDING_API_KEY") or LLM_API_KEY
    model = os.getenv(
        "EMBEDDING_MODEL",
        "BAAI/bge-small-en-v1.5",
    )
    provider = (
        HuggingFaceEmbeddingProvider(api_key, model=model)
        if api_key
        else None
    )
    return EmbeddingService(provider)