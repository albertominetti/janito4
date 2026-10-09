"""Haiku pricing uses the whole prompt size, including cache reads."""

import pytest

from janito.providers.anthropic.cost import get_cost
from janito.providers.costing import get_provider_cost


@pytest.mark.parametrize(
    "input_tokens,cached,output_tokens,expected",
    [
        (0, 0, 0, 0.0),
        (99_999, 0, 10_000, 0.0149999),
        (100_000, 0, 10_000, 0.015),
        (100_001, 0, 10_000, 0.0750005),
        (100_000, 40_000, 10_000, 0.0114),
        (100_001, 40_000, 10_000, 0.0570005),
        (100_001, 100_001, 10_000, 0.03000005),
        (1_000_000, 500_000, 128_000, 0.595),
    ],
)
def test_haiku_cost_tiers(input_tokens, cached, output_tokens, expected):
    cost = get_cost("claude-haiku-5-5", input_tokens, output_tokens, cached)
    assert float(cost.removesuffix("$")) == pytest.approx(expected, abs=0.000001)
    assert get_cost(
        "claude-haiku-5-5", input_tokens, output_tokens, cached, is_reference=True
    ) == cost


def test_haiku_cost_provider_dispatch():
    assert get_provider_cost("Anthropic", "claude-haiku-5-5", 1_000_000, 1_000_000, 0) != "N/A"
