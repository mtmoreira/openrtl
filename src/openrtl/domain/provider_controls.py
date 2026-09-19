"""Reviewed provider selections and conservative OpenAI spend estimates."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import cast

from openrtl.domain.design_session import JsonObject, require


# Standard text USD per million tokens, verified against the linked model pages.
# This is a fail-closed compatibility catalog, not an account-access assertion.
OPENAI_MODELS = {
    "gpt-5.4-nano-2026-03-17": (Decimal("0.20"), Decimal("1.25")),
    "gpt-6-astra": (Decimal("10"), Decimal("50")),
    "gpt-5.6-sol": (Decimal("4"), Decimal("20")),
    "gpt-5.6-terra": (Decimal("2"), Decimal("12")),
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")),
    "gpt-4.1": (Decimal("2"), Decimal("8")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4o": (Decimal("2.50"), Decimal("10")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
}
MODELS = OPENAI_MODELS
CATALOG_SOURCE = "https://developers.openai.com/api/docs/models"
CONTEXT_TOKENS = {model: 1_050_000 for model in OPENAI_MODELS}
CONTEXT_TOKENS["gpt-5.4-nano-2026-03-17"] = 400_000
CONTEXT_TOKENS.update({"gpt-4.1": 1_047_576, "gpt-4.1-mini": 1_047_576,
                       "gpt-4o": 128_000, "gpt-4o-mini": 128_000})
MAX_OUTPUT_TOKENS = {model: 32_768 for model in OPENAI_MODELS}
MAX_OUTPUT_TOKENS.update({"gpt-4o": 16_384, "gpt-4o-mini": 16_384})
HIGH_CONTEXT_MODELS = (frozenset(OPENAI_MODELS) -
                       {"gpt-5.4-nano-2026-03-17", "gpt-4o", "gpt-4o-mini"})
HIGH_CONTEXT_THRESHOLD = 272_000
NANO_USD = 1_000_000_000
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_SELECTOR_PREFIX = "ollama/"


def compatible_model(model: object) -> str:
    require(type(model) is str and model in OPENAI_MODELS, "provider_model_incompatible")
    return cast(str, model)


def compatible_ollama_model(model: object) -> str:
    require(type(model) is str and re.fullmatch(
        r"(?=.{1,128}\Z)[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?",
        model) is not None, "provider_model_incompatible")
    return cast(str, model)


def provider_selector(provider: object, model: object) -> str:
    require(provider in ("openai", "ollama"), "provider_kind_invalid")
    if provider == "openai":
        return compatible_model(model)
    return OLLAMA_SELECTOR_PREFIX + compatible_ollama_model(model)


def validate_provider_selector(selector: object) -> str:
    require(type(selector) is str, "provider_model_incompatible")
    value = cast(str, selector)
    if value.startswith(OLLAMA_SELECTOR_PREFIX):
        return provider_selector("ollama", value[len(OLLAMA_SELECTOR_PREFIX):])
    return compatible_model(value)


def selector_provider(selector: str) -> str:
    selected = validate_provider_selector(selector)
    return "ollama" if selected.startswith(OLLAMA_SELECTOR_PREFIX) else "openai"


def selector_model(selector: str) -> str:
    selected = validate_provider_selector(selector)
    return selected[len(OLLAMA_SELECTOR_PREFIX):] if selected.startswith(OLLAMA_SELECTOR_PREFIX) else selected


def spend_limit_nano(value: object) -> int:
    require(type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,3})\.[0-9]{2}", value) is not None,
            "provider_spend_limit_invalid")
    cents = int(Decimal(cast(str, value)) * 100)
    require(1 <= cents <= 100_000, "provider_spend_limit_invalid")
    return cents * 10_000_000


def dollars(nano: int) -> str:
    require(type(nano) is int and nano >= 0, "provider_spend_invalid")
    return format(Decimal(nano) / NANO_USD, ".6f")


def estimated_cost_nano(model: str, input_tokens: int, output_tokens: int) -> int:
    compatible_model(model)
    require(all(type(value) is int and value >= 0 for value in (input_tokens, output_tokens)),
            "provider_usage_invalid")
    input_rate, output_rate = OPENAI_MODELS[model]
    if model in HIGH_CONTEXT_MODELS and input_tokens > HIGH_CONTEXT_THRESHOLD:
        input_rate *= 2
        output_rate *= Decimal("1.5")
    cost = (input_rate * input_tokens + output_rate * output_tokens) * 1000
    return int(cost.to_integral_value(rounding="ROUND_CEILING"))


def request_reserve_nano(model: str, max_output_tokens: int) -> int:
    compatible_model(model)
    require(type(max_output_tokens) is int and 256 <= max_output_tokens <= MAX_OUTPUT_TOKENS[model],
            "output_budget_invalid")
    return estimated_cost_nano(model, CONTEXT_TOKENS[model], max_output_tokens)


def model_catalog() -> list[dict[str, str]]:
    return [{"id": identifier, "input_usd_per_million": str(rates[0]),
             "output_usd_per_million": str(rates[1]),
             "context_tokens": str(CONTEXT_TOKENS[identifier]),
             "max_output_tokens": str(MAX_OUTPUT_TOKENS[identifier]), "source": CATALOG_SOURCE}
            for identifier, rates in OPENAI_MODELS.items()]


def ollama_catalog() -> JsonObject:
    return {"provider": "ollama", "host": OLLAMA_HOST, "models": "installed_model_name",
            "compatibility": "validated_by_bounded_structured_output",
            "api_key": False, "usd_spend_estimate": False}
