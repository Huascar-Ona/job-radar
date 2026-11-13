"""Versioned pricing for cost *estimates*. Nothing here is billing.

A pricing table is a TOML file::

    schema_version = 1
    version = "anthropic-2026-11-01"     # a name for this set of rates
    currency = "USD"
    source = "https://..."               # where the rates were read
    retrieved = 2026-11-01               # when they were read
    unit = "per_million_tokens"

    [[rates]]
    model = "claude-opus-5-5"
    service_tier = "standard"            # conditions the rates apply under,
    speed = "standard"                   # matched against each call's usage
    inference_geo = "global"
    min_request_input_tokens = 0         # the request sizes this rate covers:
    max_request_input_tokens = "none"    # an integer, or "none" for no limit
    input = "..."                        # decimal strings, never floats
    output = "..."
    cache_write_5m = "..."
    cache_write_1h = "..."
    cache_read = "..."

A call is priced only by a rate whose model and conditions all match it and
whose request-size range contains the call's request input (uncached input
+ cache writes + cache reads). The range is mandatory, with ``"none"`` stated
explicitly for no upper limit, because some models price long requests
differently; whoever enters a rate must say which sizes it covers. Anything
else (an unknown model, other conditions, a size outside every range,
unknown usage, cache writes without a lifetime) makes the estimate unknown:
``None``, never 0.
"""

import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from tools.harness_metrics.transcript import ApiCall

SCHEMA_VERSION: Final = 1
MILLION: Final = Decimal(1_000_000)
RATE_FIELDS: Final = (
    "input",
    "output",
    "cache_write_5m",
    "cache_write_1h",
    "cache_read",
)
CONDITION_FIELDS: Final = ("service_tier", "speed", "inference_geo")
RANGE_FIELDS: Final = ("min_request_input_tokens", "max_request_input_tokens")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


class PricingError(ValueError):
    """The pricing table is invalid. The message names the problem only."""


@dataclass(frozen=True, slots=True)
class Rate:
    model: str
    service_tier: str
    speed: str
    inference_geo: str
    min_request_input_tokens: int
    #: ``None`` when the table states explicitly that there is no limit.
    max_request_input_tokens: int | None
    #: Per million tokens, in the table's currency.
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal

    def covers(self, request_input: int) -> bool:
        if request_input < self.min_request_input_tokens:
            return False
        high = self.max_request_input_tokens
        return high is None or request_input <= high


@dataclass(frozen=True, slots=True)
class PricingTable:
    version: str
    currency: str
    source: str
    retrieved: date
    rates: tuple[Rate, ...]

    def rate_for(self, call: ApiCall, request_input: int) -> Rate | None:
        for rate in self.rates:
            if (
                rate.model == call.model
                and rate.service_tier == call.service_tier
                and rate.speed == call.speed
                and rate.inference_geo == call.inference_geo
                and rate.covers(request_input)
            ):
                return rate
        return None


class Unpriced:
    """Why a call could not be priced."""

    UNKNOWN_USAGE: Final = "unknown_usage"
    NO_RATE: Final = "no_matching_rate"
    CACHE_LIFETIME_UNKNOWN: Final = "cache_write_lifetime_unknown"


@dataclass(frozen=True, slots=True)
class Estimate:
    pricing_version: str
    currency: str
    #: ``None`` unless every counted call was priced.
    amount: Decimal | None
    priced_calls: int
    unpriced_calls: int
    unpriced_reasons: dict[str, int]
    label: str = "estimate"


def load_pricing(path: Path) -> PricingTable:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        msg = "pricing table is not valid TOML"
        raise PricingError(msg) from None
    return parse_pricing(data)


def parse_pricing(data: dict[str, object]) -> PricingTable:
    if data.get("schema_version") != SCHEMA_VERSION:
        msg = f"schema_version must be {SCHEMA_VERSION}"
        raise PricingError(msg)
    version = data.get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        msg = "version must be a short identifier"
        raise PricingError(msg)
    currency = data.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        msg = "currency must be a three-letter code"
        raise PricingError(msg)
    if data.get("unit") != "per_million_tokens":
        msg = "unit must be 'per_million_tokens'"
        raise PricingError(msg)
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        msg = "source must name where the rates were read"
        raise PricingError(msg)
    retrieved = data.get("retrieved")
    if not isinstance(retrieved, date):
        msg = "retrieved must be a TOML date"
        raise PricingError(msg)
    raw_rates = data.get("rates", [])
    if not isinstance(raw_rates, list):
        msg = "rates must be an array of tables"
        raise PricingError(msg)
    rates = tuple(_rate(i, raw) for i, raw in enumerate(raw_rates))
    for i, a in enumerate(rates):
        for b in rates[i + 1 :]:
            same = (a.model, a.service_tier, a.speed, a.inference_geo) == (
                b.model,
                b.service_tier,
                b.speed,
                b.inference_geo,
            )
            if same and _overlap(a, b):
                msg = "two rates share a model, conditions and request sizes"
                raise PricingError(msg)
    return PricingTable(version, currency, source.strip(), retrieved, rates)


def _rate(index: int, raw: object) -> Rate:
    if not isinstance(raw, dict):
        msg = f"rates[{index}] must be a table"
        raise PricingError(msg)
    expected = {"model", *CONDITION_FIELDS, *RANGE_FIELDS, *RATE_FIELDS}
    if set(raw) != expected:
        msg = f"rates[{index}] must have exactly: {', '.join(sorted(expected))}"
        raise PricingError(msg)
    text = {name: raw[name] for name in ("model", *CONDITION_FIELDS)}
    for name, value in text.items():
        if not isinstance(value, str) or not value:
            msg = f"rates[{index}].{name} must be a non-empty string"
            raise PricingError(msg)
    amounts: dict[str, Decimal] = {}
    for name in RATE_FIELDS:
        value = raw[name]
        # Strings only: a TOML float would already have lost precision.
        if not isinstance(value, str):
            msg = f"rates[{index}].{name} must be a decimal string"
            raise PricingError(msg)
        try:
            amount = Decimal(value)
        except InvalidOperation:
            msg = f"rates[{index}].{name} is not a decimal"
            raise PricingError(msg) from None
        if not amount.is_finite() or amount < 0:
            msg = f"rates[{index}].{name} must be a finite, non-negative decimal"
            raise PricingError(msg)
        amounts[name] = amount
    low = raw["min_request_input_tokens"]
    if isinstance(low, bool) or not isinstance(low, int) or low < 0:
        msg = f"rates[{index}].min_request_input_tokens must be a non-negative integer"
        raise PricingError(msg)
    high = raw["max_request_input_tokens"]
    if high == "none":
        high = None
    elif isinstance(high, bool) or not isinstance(high, int) or high < low:
        msg = (
            f"rates[{index}].max_request_input_tokens must be an integer not below"
            ' the minimum, or "none"'
        )
        raise PricingError(msg)
    return Rate(
        model=str(text["model"]),
        service_tier=str(text["service_tier"]),
        speed=str(text["speed"]),
        inference_geo=str(text["inference_geo"]),
        min_request_input_tokens=low,
        max_request_input_tokens=high,
        **amounts,
    )


def _overlap(a: Rate, b: Rate) -> bool:
    a_high = a.max_request_input_tokens
    b_high = b.max_request_input_tokens
    return (b_high is None or a.min_request_input_tokens <= b_high) and (
        a_high is None or b.min_request_input_tokens <= a_high
    )


def estimate(calls: Iterable[ApiCall], table: PricingTable | None) -> Estimate | None:
    """The estimated cost of ``calls``, or why it is unknown.

    ``None`` when no pricing table is configured at all. Synthetic and
    subagent calls are excluded, as in ``aggregate.total``.
    """
    if table is None:
        return None
    counted = [c for c in calls if not c.synthetic and not c.sidechain]
    amount = Decimal(0)
    reasons: dict[str, int] = {}
    priced = 0
    for call in counted:
        reason, cost = _price(call, table)
        if reason is not None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        priced += 1
        amount += cost
    unpriced = len(counted) - priced
    return Estimate(
        pricing_version=table.version,
        currency=table.currency,
        amount=amount if unpriced == 0 else None,
        priced_calls=priced,
        unpriced_calls=unpriced,
        unpriced_reasons=dict(sorted(reasons.items())),
    )


def _price(call: ApiCall, table: PricingTable) -> tuple[str | None, Decimal]:
    usage = call.usage
    if usage is None:
        return Unpriced.UNKNOWN_USAGE, Decimal(0)
    rate = table.rate_for(call, usage.request_input)
    if rate is None:
        return Unpriced.NO_RATE, Decimal(0)
    if usage.cache_write_unspecified:
        return Unpriced.CACHE_LIFETIME_UNKNOWN, Decimal(0)
    tokens: Sequence[tuple[int, Decimal]] = (
        (usage.input, rate.input),
        (usage.output, rate.output),
        (usage.cache_write_5m, rate.cache_write_5m),
        (usage.cache_write_1h, rate.cache_write_1h),
        (usage.cache_read, rate.cache_read),
    )
    return None, sum((count * per for count, per in tokens), Decimal(0)) / MILLION
