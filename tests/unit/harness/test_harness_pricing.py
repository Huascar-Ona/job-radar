"""H1-2: versioned pricing, estimates and their unknowns.

The rates below are invented for the tests (round numbers chosen so the
arithmetic is checkable by hand); they are not Anthropic's prices.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import tools.harness_metrics
from tools.harness_metrics.pricing import (
    PricingError,
    estimate,
    load_pricing,
    parse_pricing,
)
from tools.harness_metrics.transcript import ApiCall, Usage

REPO = Path(tools.harness_metrics.__file__).parents[2]


def table_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": 1,
        "version": "test-rates-1",
        "currency": "USD",
        "unit": "per_million_tokens",
        "source": "invented for tests",
        "retrieved": date(2026, 11, 12),
        "rates": [
            {
                "model": "model-a",
                "service_tier": "standard",
                "speed": "standard",
                "inference_geo": "global",
                "min_request_input_tokens": 0,
                "max_request_input_tokens": "none",
                "input": "10",
                "output": "50",
                "cache_write_5m": "12.5",
                "cache_write_1h": "20",
                "cache_read": "1",
            }
        ],
    }
    data.update(overrides)
    return data


def call(
    message_id: str = "m1",
    *,
    model: str = "model-a",
    usage: Usage | None = None,
    speed: str = "standard",
    geo: str = "global",
) -> ApiCall:
    return ApiCall(
        message_id=message_id,
        timestamp=None,
        model=model,
        service_tier="standard",
        speed=speed,
        inference_geo=geo,
        stop_reason="end_turn",
        usage=usage
        or Usage(
            input=1_000_000,
            output=200_000,
            cache_write_5m=400_000,
            cache_write_1h=100_000,
            cache_write_unspecified=0,
            cache_read=3_000_000,
            thinking=None,
        ),
        sidechain=False,
    )


def test_every_category_is_priced_exactly() -> None:
    table = parse_pricing(table_data())

    result = estimate([call()], table)

    # 1.0*10 + 0.2*50 + 0.4*12.5 + 0.1*20 + 3.0*1 = 10 + 10 + 5 + 2 + 3
    assert result is not None
    assert result.amount == Decimal(30)
    assert (result.priced_calls, result.unpriced_calls) == (1, 0)
    assert result.label == "estimate"
    assert result.pricing_version == "test-rates-1"


@pytest.mark.parametrize(
    ("unpriced", "reason"),
    [
        (call("m2", model="model-b"), "no_matching_rate"),
        (call("m2", speed="fast"), "no_matching_rate"),
        (call("m2", geo="us"), "no_matching_rate"),
        (replace(call("m2"), usage=None), "unknown_usage"),
        (
            call(
                "m2",
                usage=Usage(
                    1, 1, 0, 0, cache_write_unspecified=5, cache_read=0, thinking=None
                ),
            ),
            "cache_write_lifetime_unknown",
        ),
    ],
)
def test_one_unpriced_call_makes_the_estimate_unknown_not_zero(
    unpriced: ApiCall, reason: str
) -> None:
    result = estimate([call(), unpriced], parse_pricing(table_data()))

    assert result is not None
    assert result.amount is None
    assert (result.priced_calls, result.unpriced_calls) == (1, 1)
    assert result.unpriced_reasons == {reason: 1}


def test_synthetic_and_sidechain_calls_are_not_priced() -> None:
    synthetic = call("m2", model="<synthetic>")
    side = replace(call("m3"), sidechain=True)

    result = estimate([call(), synthetic, side], parse_pricing(table_data()))

    assert result is not None
    assert result.amount == Decimal(30)
    assert result.priced_calls == 1


def test_no_table_means_no_estimate() -> None:
    assert estimate([call()], None) is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"version": "bad version!"}, "version"),
        ({"currency": "usd"}, "currency"),
        ({"unit": "per_token"}, "unit"),
        ({"source": " "}, "source"),
        ({"retrieved": "2026-11-12"}, "retrieved"),
        ({"rates": {"model": "x"}}, "array"),
    ],
)
def test_table_metadata_is_validated(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(PricingError, match=message):
        parse_pricing(table_data(**overrides))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input", 10.0),  # floats would already have lost precision
        ("output", "-1"),
        ("cache_read", "NaN"),
        ("cache_write_1h", "twenty"),
    ],
)
def test_rates_must_be_non_negative_decimal_strings(field: str, value: object) -> None:
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    rates[0][field] = value

    with pytest.raises(PricingError, match=field):
        parse_pricing(data)


def test_rates_need_every_field_and_no_other() -> None:
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    del rates[0]["cache_read"]

    with pytest.raises(PricingError, match="exactly"):
        parse_pricing(data)


def test_overlapping_rates_are_rejected() -> None:
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    rates.append(dict(rates[0]) | {"min_request_input_tokens": 100})

    with pytest.raises(PricingError, match="share"):
        parse_pricing(data)


def tiered_table() -> dict[str, object]:
    """Two tiers for one model: up to 200 tokens, and above."""
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    base = rates[0]
    rates[0] = base | {"max_request_input_tokens": 200}
    rates.append(
        base | {"min_request_input_tokens": 201, "input": "20", "output": "100"}
    )
    return data


def small(input_tokens: int) -> Usage:
    return Usage(input_tokens, 1_000_000, 0, 0, 0, cache_read=0, thinking=None)


def test_request_size_selects_the_rate_tier() -> None:
    table = parse_pricing(tiered_table())

    low = estimate([call(usage=small(200))], table)
    high = estimate([call(usage=small(201))], table)

    # 200 * 10 / 1e6 + 1.0 * 50 ; 201 * 20 / 1e6 + 1.0 * 100
    assert low is not None
    assert high is not None
    assert low.amount == Decimal("50.002")
    assert high.amount == Decimal("100.00402")


def test_a_request_outside_every_range_is_unpriced() -> None:
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    rates[0]["max_request_input_tokens"] = 100

    result = estimate([call(usage=small(101))], parse_pricing(data))

    assert result is not None
    assert result.amount is None
    assert result.unpriced_reasons == {"no_matching_rate": 1}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_request_input_tokens", None),
        ("max_request_input_tokens", "unlimited"),
        ("max_request_input_tokens", -1),
        ("min_request_input_tokens", -1),
        ("min_request_input_tokens", True),
    ],
)
def test_the_request_size_range_must_be_explicit(field: str, value: object) -> None:
    data = table_data()
    rates = data["rates"]
    assert isinstance(rates, list)
    if value is None:
        del rates[0][field]
    else:
        rates[0][field] = value

    with pytest.raises(PricingError, match=r"request_input_tokens|exactly"):
        parse_pricing(data)


def test_the_committed_table_is_valid_and_prices_nothing() -> None:
    # D4: rates are entered by the maintainer from the official page; until
    # then every estimate is unknown.
    table = load_pricing(REPO / "docs" / "metrics" / "pricing.toml")

    result = estimate([call(model="claude-opus-5-5")], table)

    assert table.rates == ()
    assert result is not None
    assert result.amount is None
    assert result.unpriced_reasons == {"no_matching_rate": 1}


def test_invalid_toml_is_reported_without_its_content(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text('version = "x"\nsource = secret-value\n', encoding="utf-8")

    with pytest.raises(PricingError, match="not valid TOML") as caught:
        load_pricing(path)

    assert "secret-value" not in str(caught.value)
