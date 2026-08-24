import hashlib
import math
import re
from pathlib import Path
from threading import Lock
from typing import Literal
import time

from app.core.config import Settings, get_settings
from app.services.observability import BudgetExceededError, observability_service

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - only used when optional install is missing
    OpenAI = None

try:
    from fastembed import TextEmbedding
except ImportError:  # pragma: no cover - surfaced by provider_unavailable_reason
    TextEmbedding = None

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")
OPENAI_DIMENSIONALITY_MODELS = ("text-embedding-3",)


class EmbeddingService:
    def __init__(self) -> None:
        self._model = None
        self._model_key: tuple[str, str, int | None] | None = None
        self._model_lock = Lock()

    def embed(
        self,
        text: str,
        provider: str | None = None,
        *,
        input_type: Literal["query", "document"] = "document",
    ) -> list[float]:
        return self.embed_many([text], provider=provider, input_type=input_type)[0]

    def embed_many(
        self,
        texts: list[str],
        provider: str | None = None,
        *,
        input_type: Literal["query", "document"] = "document",
    ) -> list[list[float]]:
        settings = get_settings()
        if not texts:
            return []

        provider = (provider or settings.embedding_provider).lower().strip()
        model = self.model_for_provider(provider)
        input_units = sum(self._estimate_tokens(text) for text in texts)
        estimated_cost = (
            input_units * settings.openai_embedding_cost_per_million_tokens / 1_000_000
            if provider == "openai"
            else 0.0
        )
        started = time.perf_counter()
        try:
            observability_service.assert_budget_available(estimated_cost)
            if provider == "local":
                vectors = self._local_embed_many(
                    texts,
                    settings=settings,
                    input_type=input_type,
                )
            elif provider == "openai":
                vectors = self._openai_embed_many(texts=texts, settings=settings)
            else:
                raise ValueError("APP_EMBEDDING_PROVIDER must be 'local' or 'openai'.")
        except Exception as exc:
            observability_service.record_safely(
                operation_type="embedding",
                provider=provider,
                model=model,
                status="blocked" if isinstance(exc, BudgetExceededError) else "failed",
                latency_ms=(time.perf_counter() - started) * 1_000,
                input_units=input_units,
                estimated_cost_usd=0.0,
                metadata={
                    "batch_size": len(texts),
                    "error": str(exc),
                    "unit_estimation": "characters_divided_by_4",
                },
            )
            raise

        observability_service.record_safely(
            operation_type="embedding",
            provider=provider,
            model=model,
            status="completed",
            latency_ms=(time.perf_counter() - started) * 1_000,
            input_units=input_units,
            output_units=len(vectors) * settings.vector_dimensions,
            estimated_cost_usd=estimated_cost,
            metadata={
                "batch_size": len(texts),
                "dimensions": settings.vector_dimensions,
                "input_type": input_type,
                "local_backend": (
                    settings.local_embedding_backend if provider == "local" else ""
                ),
                "unit_estimation": "characters_divided_by_4",
            },
        )
        return vectors

    def model_for_provider(self, provider: str) -> str:
        settings = get_settings()
        normalized = provider.lower().strip()
        if normalized == "local":
            if settings.local_embedding_backend == "fastembed":
                return settings.local_embedding_model
            return f"local-lexical-{settings.vector_dimensions}d-v2"
        if normalized == "openai":
            return settings.openai_embedding_model
        raise ValueError("Embedding provider must be 'local' or 'openai'.")

    def provider_unavailable_reason(self, provider: str) -> str | None:
        normalized = provider.lower().strip()
        if normalized == "local":
            settings = get_settings()
            if settings.local_embedding_backend == "fastembed" and TextEmbedding is None:
                return "The fastembed package is not installed."
            return None
        if normalized != "openai":
            return "Embedding provider must be 'local' or 'openai'."
        settings = get_settings()
        if not (settings.openai_api_key or "").strip():
            return "OPENAI_API_KEY is not configured."
        if OpenAI is None:
            return "The openai package is not installed."
        return None

    def _local_embed_many(
        self,
        texts: list[str],
        *,
        settings: Settings,
        input_type: Literal["query", "document"],
    ) -> list[list[float]]:
        if settings.local_embedding_backend == "lexical":
            return [
                self._lexical_embed(text, dimensions=settings.vector_dimensions)
                for text in texts
            ]

        model = self._fastembed_model(settings)
        if input_type == "query":
            vectors = model.query_embed(texts)
        else:
            vectors = model.passage_embed(texts)
        materialized = [vector.tolist() for vector in vectors]
        if materialized and len(materialized[0]) != settings.vector_dimensions:
            raise ValueError(
                f"Local model {settings.local_embedding_model} emits "
                f"{len(materialized[0])} dimensions, but APP_VECTOR_DIMENSIONS is "
                f"{settings.vector_dimensions}. Reconfigure the dimensions before indexing."
            )
        return materialized

    def _fastembed_model(self, settings: Settings):
        if TextEmbedding is None:
            raise ValueError("The fastembed package is required for local semantic embeddings.")
        key = (
            settings.local_embedding_model,
            settings.local_embedding_cache_dir,
            settings.local_embedding_threads,
        )
        with self._model_lock:
            if self._model is None or self._model_key != key:
                cache_dir = Path(settings.local_embedding_cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=settings.local_embedding_model,
                    cache_dir=str(cache_dir),
                    threads=settings.local_embedding_threads,
                    lazy_load=True,
                )
                model_dimensions = TextEmbedding.get_embedding_size(
                    settings.local_embedding_model
                )
                if model_dimensions != settings.vector_dimensions:
                    self._model = None
                    raise ValueError(
                        f"Local model {settings.local_embedding_model} uses "
                        f"{model_dimensions} dimensions; APP_VECTOR_DIMENSIONS must match."
                    )
                self._model_key = key
        return self._model

    def _lexical_embed(self, text: str, dimensions: int) -> list[float]:
        """Offline test/failsafe vectors with word, bigram, and character features."""
        vector = [0.0] * dimensions
        tokens = TOKEN_PATTERN.findall(text.lower())
        if not tokens:
            return vector

        features = list(tokens)
        features.extend(
            f"{left}::{right}" for left, right in zip(tokens, tokens[1:])
        )
        for token in tokens:
            padded = f"^{token}$"
            features.extend(
                padded[index : index + 3]
                for index in range(max(0, len(padded) - 2))
            )

        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [value / magnitude for value in vector]

    def _openai_embed_many(self, texts: list[str], settings: Settings) -> list[list[float]]:
        api_key = (settings.openai_api_key or "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when APP_EMBEDDING_PROVIDER=openai.")
        if OpenAI is None:
            raise ValueError("The openai package is required for OpenAI embeddings.")

        model = settings.openai_embedding_model
        request = {
            "input": [self._normalize_input(text) for text in texts],
            "model": model,
            "encoding_format": "float",
        }
        if model.startswith(OPENAI_DIMENSIONALITY_MODELS):
            request["dimensions"] = settings.vector_dimensions
        elif settings.vector_dimensions != 1536:
            raise ValueError(
                "This OpenAI embedding model does not support custom dimensions. "
                "Use text-embedding-3-small/large or set APP_VECTOR_DIMENSIONS=1536."
            )

        client = OpenAI(
            api_key=api_key,
            timeout=settings.openai_embedding_timeout_seconds,
        )
        response = client.embeddings.create(**request)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]

    def _normalize_input(self, text: str) -> str:
        cleaned = text.replace("\n", " ").strip()
        return cleaned or " "

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot_product = sum(a * b for a, b in zip(left, right))
        left_magnitude = math.sqrt(sum(value * value for value in left))
        right_magnitude = math.sqrt(sum(value * value for value in right))
        if left_magnitude == 0 or right_magnitude == 0:
            return 0.0
        return dot_product / (left_magnitude * right_magnitude)


embedding_service = EmbeddingService()
