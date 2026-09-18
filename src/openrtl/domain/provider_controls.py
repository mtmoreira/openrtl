"""Reviewed OpenAI Responses structured-output models and local spend estimates."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import cast

from openrtl.domain.design_session import require


# Standard text USD per million tokens, verified against the linked model pages.
# This is a fail-closed compatibility catalog, not an account-access assertion.
MODELS = {
    "gpt-5.4-nano-2026-03-17": (Decimal("0.20"), Decimal("1.25")),
    "gpt-6-astra": (Decimal("10"), Decimal("50")),
    "gpt-5.6-sol": (Decimal("4"), Decimal("20")),
    "gpt-5.6-terra": (Decimal("2"), Decimal("12")),
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")),
}
CATALOG_SOURCE = "https://developers.openai.com/api/docs/models"
CONTEXT_TOKENS = {model: 1_050_000 for model in MODELS}
CONTEXT_TOKENS["gpt-5.4-nano-2026-03-17"] = 400_000
HIGH_CONTEXT_MODELS = frozenset(MODELS) - {"gpt-5.4-nano-2026-03-17"}
HIGH_CONTEXT_THRESHOLD = 272_000
NANO_USD = 1_000_000_000


def compatible_model(model: object) -> str:
    require(type(model) is str and model in MODELS, "provider_model_incompatible")
    return cast(str, model)


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
    input_rate, output_rate = MODELS[model]
    if model in HIGH_CONTEXT_MODELS and input_tokens > HIGH_CONTEXT_THRESHOLD:
        input_rate *= 2
        output_rate *= Decimal("1.5")
    cost = (input_rate * input_tokens + output_rate * output_tokens) * 1000
    return int(cost.to_integral_value(rounding="ROUND_CEILING"))


def request_reserve_nano(model: str, max_output_tokens: int) -> int:
    require(type(max_output_tokens) is int and 256 <= max_output_tokens <= 32768,
            "output_budget_invalid")
    compatible_model(model)
    return estimated_cost_nano(model, CONTEXT_TOKENS[model], max_output_tokens)


def model_catalog() -> list[dict[str, str]]:
    return [{"id": identifier, "input_usd_per_million": str(rates[0]),
             "output_usd_per_million": str(rates[1]),
             "context_tokens": str(CONTEXT_TOKENS[identifier]), "source": CATALOG_SOURCE}
            for identifier, rates in MODELS.items()]
