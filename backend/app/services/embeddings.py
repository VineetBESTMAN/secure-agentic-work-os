import hashlib
import math
import re

from app.core.config import Settings, get_settings

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - only used when optional install is missing
    OpenAI = None

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")
OPENAI_DIMENSIONALITY_MODELS = ("text-embedding-3",)


class EmbeddingService:
    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        if not texts:
            return []

        provider = settings.embedding_provider.lower().strip()
        if provider == "local":
            return [self._local_embed(text, dimensions=settings.vector_dimensions) for text in texts]
        if provider == "openai":
            return self._openai_embed_many(texts=texts, settings=settings)
        raise ValueError("APP_EMBEDDING_PROVIDER must be 'local' or 'openai'.")

    def _local_embed(self, text: str, dimensions: int) -> list[float]:
        vector = [0.0] * dimensions
        tokens = TOKEN_PATTERN.findall(text.lower())
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
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
