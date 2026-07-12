from types import SimpleNamespace

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
    assert calls["request"] == {
        "input": ["first text", "second text"],
        "model": "text-embedding-3-small",
        "encoding_format": "float",
        "dimensions": 4,
    }
    assert vectors == [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
