from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.schemas import Citation, UserContext
from app.services.agent import agent_service
from app.services.grounded_answers import grounded_answer_service


client = TestClient(app)


class FakeOpenAI:
    responder = None
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs) -> None:
        assert kwargs["max_retries"] == 0
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = type(self).responder(kwargs)
        return SimpleNamespace(
            output_parsed=output,
            usage=SimpleNamespace(input_tokens=100, output_tokens=25),
        )


@pytest.fixture(autouse=True)
def reset_model_settings(monkeypatch):
    monkeypatch.setenv("APP_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_GENERATION_MODEL", "test-structured-model")
    monkeypatch.setenv("APP_GROUNDED_ANSWERS_ENABLED", "true")
    monkeypatch.setenv("APP_LLM_PLANNER_ENABLED", "true")
    monkeypatch.setattr("app.services.model_gateway.OpenAI", FakeOpenAI)
    monkeypatch.setattr("app.services.model_gateway.time.sleep", lambda _delay: None)
    FakeOpenAI.calls = []
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _admin_user() -> UserContext:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@demo.local", "password": "demo-password"},
    )
    assert response.status_code == 200
    return UserContext.model_validate(response.json()["user"])


def test_grounded_generation_requires_retrieved_citation_ids() -> None:
    def respond(kwargs):
        return kwargs["text_format"].model_validate(
            {
                "claims": [
                        {
                            "text": "Escalations must be triaged within one hour.",
                            "supports": [
                                {
                                    "citation_id": "chk_allowed",
                                    "quote": "triaged within one hour",
                                }
                            ],
                    }
                ],
                "insufficient_evidence": False,
            }
        )

    FakeOpenAI.responder = respond
    result = grounded_answer_service.generate(
        question="How quickly must escalations be triaged?",
        citations=[
            Citation(
                document_id="doc_1",
                title="Escalation policy",
                excerpt="Escalations must be triaged within one hour.",
                chunk_id="chk_allowed",
                score=0.9,
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
    )

    assert result.answer.endswith("[1]")
    assert result.generation_mode == "openai"
    assert result.model == "test-structured-model"
    assert result.grounded is True
    assert FakeOpenAI.calls[0]["store"] is False
    assert FakeOpenAI.calls[0]["max_output_tokens"] == 1200


@pytest.mark.parametrize(
    ("citation_id", "supporting_quote"),
    [
        ("chk_invented", "An unsupported claim"),
        ("chk_real", "This quote does not occur in the evidence"),
    ],
)
def test_unsupported_model_citation_is_rejected_and_falls_back(
    citation_id: str, supporting_quote: str
) -> None:
    def respond(kwargs):
        return kwargs["text_format"].model_validate(
            {
                "claims": [
                    {
                        "text": "An unsupported claim.",
                        "supports": [
                            {
                                "citation_id": citation_id,
                                "quote": supporting_quote,
                            }
                        ],
                    }
                ]
            }
        )

    FakeOpenAI.responder = respond
    result = grounded_answer_service.generate(
        question="What is required?",
        citations=[
            Citation(
                document_id="doc_1",
                title="Policy",
                excerpt="Manager review is required before sending a summary.",
                chunk_id="chk_real",
                score=0.8,
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
    )

    assert result.generation_mode == "deterministic"
    assert result.answer.endswith("[1]")
    assert "unsupported claim" not in result.answer.lower()
    assert result.fallback_reason == (
        "OpenAI generation failed; deterministic fallback was used."
    )


def test_transient_generation_failure_is_retried(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_GENERATION_MAX_RETRIES", "1")
    get_settings.cache_clear()
    attempts = 0

    class TemporaryRateLimit(Exception):
        status_code = 429

    def respond(kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TemporaryRateLimit("retry")
        return kwargs["text_format"].model_validate(
            {
                "claims": [
                    {
                        "text": "Use manager review.",
                        "supports": [
                            {
                                "citation_id": "chk_retry",
                                "quote": "Use manager review",
                            }
                        ],
                    }
                ]
            }
        )

    FakeOpenAI.responder = respond
    result = grounded_answer_service.generate(
        question="What review is required?",
        citations=[
            Citation(
                document_id="doc_retry",
                title="Review policy",
                excerpt="Use manager review.",
                chunk_id="chk_retry",
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
    )

    assert attempts == 2
    assert result.generation_mode == "openai"


def test_generation_budget_preflight_uses_deterministic_fallback(monkeypatch) -> None:
    monkeypatch.setenv(
        "OPENAI_GENERATION_OUTPUT_COST_PER_MILLION_TOKENS", "1000000"
    )
    get_settings.cache_clear()
    FakeOpenAI.responder = lambda _kwargs: pytest.fail(
        "The provider must not be called after a failed budget preflight."
    )
    result = grounded_answer_service.generate(
        question="What is required?",
        citations=[
            Citation(
                document_id="doc_budget",
                title="Budget policy",
                excerpt="A manager must approve the request.",
                chunk_id="chk_budget",
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
    )

    assert FakeOpenAI.calls == []
    assert result.generation_mode == "deterministic"
    assert result.fallback_reason == (
        "The model budget is unavailable; deterministic fallback was used."
    )


def test_model_input_limit_prevents_provider_request(monkeypatch) -> None:
    monkeypatch.setenv("APP_MODEL_MAX_INPUT_TOKENS", "256")
    get_settings.cache_clear()
    FakeOpenAI.responder = lambda _kwargs: pytest.fail(
        "The provider must not receive over-limit input."
    )
    evidence = "Manager approval is required before distribution. " * 25
    result = grounded_answer_service.generate(
        question="What approval is required?",
        citations=[
            Citation(
                document_id="doc_limit",
                title="Distribution policy",
                excerpt=evidence,
                chunk_id="chk_limit",
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
    )

    assert FakeOpenAI.calls == []
    assert result.generation_mode == "deterministic"
    assert result.fallback_reason == (
        "Model input exceeded the configured token limit; deterministic fallback was used."
    )


def test_planner_cannot_choose_scope_or_approval() -> None:
    def respond(kwargs):
        return kwargs["text_format"].model_validate(
            {
                "summary": "Send the requested approved follow-up.",
                "actions": [
                    {
                        "tool_name": "send_email",
                        "description": "Send the follow-up after review.",
                        "arguments": {
                            "to": "client@example.com",
                            "subject": "Follow-up",
                            "body": "Approved details",
                        },
                    }
                ],
            }
        )

    FakeOpenAI.responder = respond
    plan = agent_service.build_plan("Send the client a follow-up", _admin_user())

    assert plan.planner_mode == "openai"
    assert plan.validated is True
    assert plan.actions[0].scope == "email:send"
    assert plan.actions[0].requires_approval is True
    assert plan.actions[0].arguments["to"] == "client@example.com"


def test_llm_workflow_execution_still_requires_mcp_approval() -> None:
    def respond(kwargs):
        return kwargs["text_format"].model_validate(
            {
                "summary": "Send one governed email.",
                "actions": [
                    {
                        "tool_name": "send_email",
                        "description": "Send only after review.",
                        "arguments": {
                            "to": "client@example.com",
                            "subject": "Governed follow-up",
                            "body": "Review this message before sending.",
                        },
                    }
                ],
            }
        )

    FakeOpenAI.responder = respond
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@demo.local", "password": "demo-password"},
    ).json()
    response = client.post(
        "/api/agent/workflows",
        headers={"Authorization": f"Bearer {login['access_token']}"},
        json={"prompt": "Send the client a governed follow-up"},
    )

    assert response.status_code == 200
    workflow = response.json()
    assert workflow["plan"]["planner_mode"] == "openai"
    assert workflow["status"] == "waiting_for_approval"
    assert workflow["actions"][0]["tool_name"] == "send_email"
    assert workflow["actions"][0]["execution_id"].startswith("mcp_")
    assert workflow["actions"][0]["approval_id"].startswith("apr_")


def test_unauthorized_planner_tool_reverts_to_scoped_rules() -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "employee@demo.local", "password": "demo-password"},
    )
    employee = UserContext.model_validate(response.json()["user"])

    def respond(kwargs):
        return kwargs["text_format"].model_validate(
            {
                "summary": "Attempt an unauthorized email.",
                "actions": [
                    {
                        "tool_name": "send_email",
                        "description": "Send without permission.",
                        "arguments": {"to": "client@example.com"},
                    }
                ],
            }
        )

    FakeOpenAI.responder = respond
    plan = agent_service.build_plan("Send a reply", employee)

    assert plan.planner_mode == "deterministic"
    assert [action.action_type for action in plan.actions] == ["search_documents"]
    assert plan.fallback_reason == (
        "OpenAI generation failed; deterministic fallback was used."
    )


def test_model_gateway_status_is_authenticated() -> None:
    unauthenticated = client.get("/api/models/status")
    assert unauthenticated.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": "admin@demo.local", "password": "demo-password"},
    ).json()
    response = client.get(
        "/api/models/status",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "openai"
    assert response.json()["configured"] is True
