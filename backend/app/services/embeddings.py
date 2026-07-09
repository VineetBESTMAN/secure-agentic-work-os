import hashlib
import math
import re

from app.core.config import get_settings

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")


class EmbeddingService:
    def embed(self, text: str) -> list[float]:
        dimensions = get_settings().vector_dimensions
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

    def cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        return sum(a * b for a, b in zip(left, right))


embedding_service = EmbeddingService()
