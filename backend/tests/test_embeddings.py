from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services.embeddings import EmbeddingService


def test_local_embeddings_are_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("APP_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("APP_VECTOR_DIMENSIONS", "16")
    get_settings.cache_clear()

    try:
        service = EmbeddingService()
        first = service.embed("manager approval before contract summary")
        second = service.embed("manager approval before contract summary")
    finally:
        get_settings.cache_clear()

    assert first == second
    assert len(first) == 16
    assert any(first)


def test_openai_embeddings_use_configured_model_and_dimensions(monkeypatch) -> None:
    calls = {}

    class FakeEmbeddings:
        def create(self, **request):
            calls["request"] = request
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=[0.0, 1.0, 0.0, 0.0]),
                    SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0, 0.0]),
                ]
            )

    class FakeOpenAI:
        def __init__(self, api_key: str, timeout: float) -> None:
            calls["api_key"] = api_key
            calls["timeout"] = timeout
            self.embeddings = FakeEmbeddings()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("app.services.embeddings.OpenAI", FakeOpenAI)
    monkeypatch.setenv("APP_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("APP_VECTOR_DIMENSIONS", "4")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("OPENAI_EMBEDDING_TIMEOUT_SECONDS", "7")
    get_settings.cache_clear()

    try:
        vectors = EmbeddingService().embed_many(["first\ntext", "second text"])
    finally:
        get_settings.cache_clear()

    assert calls["api_key"] == "sk-test"
    assert calls["timeout"] == 7
    assert calls["closed"] is True
    assert calls["request"] == {
        "input": ["first text", "second text"],
        "model": "text-embedding-3-small",
        "encoding_format": "float",
        "dimensions": 4,
    }
    assert vectors == [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]


def test_fastembed_uses_query_and_passage_encoders(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, list[str]]] = []

    class FakeArray(list):
        def tolist(self):
            return list(self)

    class FakeTextEmbedding:
        def __init__(self, **kwargs) -> None:
            assert kwargs["model_name"] == "test-semantic-model"
            assert kwargs["cache_dir"] == str(tmp_path)

        @staticmethod
        def get_embedding_size(model_name: str) -> int:
            assert model_name == "test-semantic-model"
            return 4

        def query_embed(self, texts):
            materialized = list(texts)
            calls.append(("query", materialized))
            return iter([FakeArray([1.0, 0.0, 0.0, 0.0]) for _ in materialized])

        def passage_embed(self, texts):
            materialized = list(texts)
            calls.append(("document", materialized))
            return iter([FakeArray([0.0, 1.0, 0.0, 0.0]) for _ in materialized])

    monkeypatch.setattr("app.services.embeddings.TextEmbedding", FakeTextEmbedding)
    monkeypatch.setenv("APP_LOCAL_EMBEDDING_BACKEND", "fastembed")
    monkeypatch.setenv("APP_LOCAL_EMBEDDING_MODEL", "test-semantic-model")
    monkeypatch.setenv("APP_LOCAL_EMBEDDING_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("APP_VECTOR_DIMENSIONS", "4")
    get_settings.cache_clear()
    try:
        service = EmbeddingService()
        query = service.embed("approval question", input_type="query")
        document = service.embed("approval policy", input_type="document")
    finally:
        get_settings.cache_clear()

    assert query == [1.0, 0.0, 0.0, 0.0]
    assert document == [0.0, 1.0, 0.0, 0.0]
    assert calls == [
        ("query", ["approval question"]),
        ("document", ["approval policy"]),
    ]


@pytest.mark.parametrize("vectors", [[], [[1.0]], [[float('nan')] * 384]])
def test_embedding_batches_reject_incomplete_wrong_sized_or_nonfinite_vectors(monkeypatch, vectors):
    service = EmbeddingService()
    monkeypatch.setattr(service, "_local_embed_many", lambda *args, **kwargs: vectors)
    with pytest.raises(ValueError, match="Embedding provider"):
        service.embed_many(["one input"])


def test_embedding_network_client_closes_on_provider_error(monkeypatch):
    calls = []
    class FailingClient:
        def __init__(self, **kwargs):
            self.embeddings = self
        def create(self, **kwargs):
            raise RuntimeError("simulated provider outage")
        def close(self):
            calls.append("closed")
    monkeypatch.setattr("app.services.embeddings.OpenAI", FailingClient)
    monkeypatch.setattr(get_settings(), "openai_api_key", "not-a-real-key")
    with pytest.raises(RuntimeError, match="simulated"):
        EmbeddingService().embed("request", provider="openai")
    assert calls == ["closed"]
