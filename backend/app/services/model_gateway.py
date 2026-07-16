from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Generic, Literal, TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.models.schemas import ModelGatewayStatus
from app.services.observability import BudgetExceededError, observability_service

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency is present in supported installs
    OpenAI = None


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)
ModelOperation = Literal["model_generation", "agent_plan"]


@dataclass(frozen=True)
class ModelGatewayResult(Generic[StructuredOutput]):
    output: StructuredOutput
    mode: Literal["openai", "deterministic"]
    provider: str
    model: str
    fallback_reason: str | None = None


class ModelGatewayService:
    """Central policy boundary for structured model calls and safe fallback."""

    def status(self) -> ModelGatewayStatus:
        settings = get_settings()
        provider = self._provider(settings)
        configured = (
            provider == "deterministic"
            or self._openai_unavailable_reason(settings) is None
        )
        return ModelGatewayStatus(
            provider=provider,
            model=(
                settings.openai_generation_model
                if provider == "openai"
                else "deterministic-fallback"
            ),
            configured=configured,
            grounded_answers_enabled=settings.grounded_answers_enabled,
            llm_planner_enabled=settings.llm_planner_enabled,
            max_input_tokens=settings.model_max_input_tokens,
            max_output_tokens=settings.openai_generation_max_output_tokens,
            timeout_seconds=settings.openai_generation_timeout_seconds,
            max_retries=settings.openai_generation_max_retries,
        )

    def generate_structured(
        self,
        *,
        operation_type: ModelOperation,
        instructions: str,
        input_text: str,
        response_model: type[StructuredOutput],
        deterministic_fallback: Callable[[], StructuredOutput],
        fallback_model: str,
        actor_id: str,
        organization_id: str,
        enabled: bool = True,
        validate_output: Callable[[StructuredOutput], None] | None = None,
    ) -> ModelGatewayResult[StructuredOutput]:
        settings = get_settings()
        provider = self._provider(settings)
        input_units = self._estimate_tokens(instructions + "\n" + input_text)

        if not enabled or provider == "deterministic":
            return self._run_fallback(
                operation_type=operation_type,
                fallback=deterministic_fallback,
                fallback_model=fallback_model,
                actor_id=actor_id,
                organization_id=organization_id,
                input_units=input_units,
                reason=None,
            )

        if input_units > settings.model_max_input_tokens:
            return self._run_fallback(
                operation_type=operation_type,
                fallback=deterministic_fallback,
                fallback_model=fallback_model,
                actor_id=actor_id,
                organization_id=organization_id,
                input_units=input_units,
                reason=(
                    "Model input exceeded the configured token limit; "
                    "deterministic fallback was used."
                ),
            )

        unavailable = self._openai_unavailable_reason(settings)
        if unavailable is not None:
            return self._run_fallback(
                operation_type=operation_type,
                fallback=deterministic_fallback,
                fallback_model=fallback_model,
                actor_id=actor_id,
                organization_id=organization_id,
                input_units=input_units,
                reason=unavailable,
            )

        model = settings.openai_generation_model
        estimated_max_cost = self._cost(
            input_units,
            settings.openai_generation_max_output_tokens,
            settings,
        )
        started = time.perf_counter()
        retries = 0
        try:
            observability_service.assert_budget_available(
                estimated_max_cost, organization_id
            )
            client = OpenAI(
                api_key=(settings.openai_api_key or "").strip(),
                timeout=settings.openai_generation_timeout_seconds,
                max_retries=0,
            )
            try:
                while True:
                    try:
                        response = client.responses.parse(
                            model=model,
                            instructions=instructions,
                            input=input_text,
                            text_format=response_model,
                            max_output_tokens=settings.openai_generation_max_output_tokens,
                            store=False,
                        )
                        parsed = response.output_parsed
                        if parsed is None:
                            raise ValueError(
                                "The model returned no validated structured output."
                            )
                        if not isinstance(parsed, response_model):
                            parsed = response_model.model_validate(parsed)
                        if validate_output is not None:
                            validate_output(parsed)
                        input_tokens, output_tokens = self._usage(response, input_units)
                        actual_cost = self._cost(input_tokens, output_tokens, settings)
                        observability_service.record_safely(
                            operation_type=operation_type,
                            provider="openai",
                            model=model,
                            status="completed",
                            latency_ms=(time.perf_counter() - started) * 1_000,
                            input_units=input_tokens,
                            output_units=output_tokens,
                            estimated_cost_usd=actual_cost,
                            metadata={
                                "retries": retries,
                                "structured_output": True,
                                "store": False,
                            },
                            actor_id=actor_id,
                            organization_id=organization_id,
                        )
                        return ModelGatewayResult(
                            output=parsed,
                            mode="openai",
                            provider="openai",
                            model=model,
                        )
                    except Exception as exc:
                        retry_limit = settings.openai_generation_max_retries
                        if retries >= retry_limit or not self._retryable(exc):
                            raise
                        retries += 1
                        time.sleep(min(0.25 * (2 ** (retries - 1)), 1.0))
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        except Exception as exc:
            blocked = isinstance(exc, BudgetExceededError)
            observability_service.record_safely(
                operation_type=operation_type,
                provider="openai",
                model=model,
                status="blocked" if blocked else "failed",
                latency_ms=(time.perf_counter() - started) * 1_000,
                input_units=input_units,
                estimated_cost_usd=0.0,
                metadata={
                    "retries": retries,
                    "error_type": type(exc).__name__,
                    "structured_output": True,
                },
                actor_id=actor_id,
                organization_id=organization_id,
            )
            reason = (
                "The model budget is unavailable; deterministic fallback was used."
                if blocked
                else "OpenAI generation failed; deterministic fallback was used."
            )
            return self._run_fallback(
                operation_type=operation_type,
                fallback=deterministic_fallback,
                fallback_model=fallback_model,
                actor_id=actor_id,
                organization_id=organization_id,
                input_units=input_units,
                reason=reason,
            )

    def _run_fallback(
        self,
        *,
        operation_type: ModelOperation,
        fallback: Callable[[], StructuredOutput],
        fallback_model: str,
        actor_id: str,
        organization_id: str,
        input_units: int,
        reason: str | None,
    ) -> ModelGatewayResult[StructuredOutput]:
        started = time.perf_counter()
        output = fallback()
        output_units = self._estimate_tokens(output.model_dump_json())
        observability_service.record_safely(
            operation_type=operation_type,
            provider="deterministic",
            model=fallback_model,
            status="completed",
            latency_ms=(time.perf_counter() - started) * 1_000,
            input_units=input_units,
            output_units=output_units,
            metadata={
                "fallback": reason is not None,
                "fallback_reason": reason or "",
                "structured_output": True,
            },
            actor_id=actor_id,
            organization_id=organization_id,
        )
        return ModelGatewayResult(
            output=output,
            mode="deterministic",
            provider="deterministic",
            model=fallback_model,
            fallback_reason=reason,
        )

    @staticmethod
    def _provider(settings: Settings) -> Literal["openai", "deterministic"]:
        provider = settings.model_provider.lower().strip()
        if provider not in {"openai", "deterministic"}:
            return "deterministic"
        return provider

    @staticmethod
    def _openai_unavailable_reason(settings: Settings) -> str | None:
        if not (settings.openai_api_key or "").strip():
            return "OPENAI_API_KEY is not configured; deterministic fallback was used."
        if OpenAI is None:
            return "The OpenAI SDK is unavailable; deterministic fallback was used."
        return None

    @staticmethod
    def _usage(response: object, estimated_input: int) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", estimated_input)
        output_tokens = getattr(usage, "output_tokens", 0)
        return max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0))

    @staticmethod
    def _cost(input_tokens: int, output_tokens: int, settings: Settings) -> float:
        return (
            input_tokens * settings.openai_generation_input_cost_per_million_tokens
            + output_tokens * settings.openai_generation_output_cost_per_million_tokens
        ) / 1_000_000

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if type(exc).__name__ in {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
        }:
            return True
        status_code = getattr(exc, "status_code", None)
        return isinstance(status_code, int) and (
            status_code in {408, 409, 429} or status_code >= 500
        )


model_gateway_service = ModelGatewayService()
