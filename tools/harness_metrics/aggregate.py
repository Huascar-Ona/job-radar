"""Totals over a set of API calls, keeping every token category apart.

Three different quantities are easy to confuse, so each is named:

- ``processed_input``: input tokens summed over every call (uncached input,
  cache writes and cache reads). The same context is re-read on each call,
  so this is cumulative processing, not context size and not cost.
- ``new_context``: tokens that entered the context for the first time
  (uncached input plus cache writes).
- ``generated``: output tokens, of which ``thinking`` is a part.

``largest_request_input`` is the largest single request's input as the
API's usage fields account it (uncached input + cache writes + cache reads).
It is accounting metadata, not a measurement of the model's context window:
it excludes that call's output, and says nothing about the window's size.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from tools.harness_metrics.transcript import ApiCall


@dataclass(slots=True)
class Totals:
    calls: int = 0
    #: Calls whose usage is unknown (incomplete or conflicting): not summed.
    calls_unknown_usage: int = 0
    #: Excluded from every sum: model ``<synthetic>``.
    synthetic_calls: int = 0
    #: Excluded from every sum: subagent records, whose format is unverified.
    sidechain_calls: int = 0
    input: int = 0
    output: int = 0
    thinking: int = 0
    #: Calls that did not report thinking tokens.
    thinking_unreported: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_write_unspecified: int = 0
    cache_read: int = 0
    largest_request_input: int = 0
    tool_use_stops: int = 0
    tools: Counter[str] = field(default_factory=Counter)
    models: Counter[str] = field(default_factory=Counter)

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h + self.cache_write_unspecified

    @property
    def processed_input(self) -> int:
        return self.input + self.cache_write + self.cache_read

    @property
    def new_context(self) -> int:
        return self.input + self.cache_write

    @property
    def generated(self) -> int:
        return self.output

    @property
    def cache_read_share(self) -> float | None:
        """Share of processed input served from cache, if any was processed."""
        if self.processed_input == 0:
            return None
        return self.cache_read / self.processed_input

    @property
    def complete(self) -> bool:
        """Every counted call had known usage."""
        return self.calls_unknown_usage == 0


def total(calls: Iterable[ApiCall]) -> Totals:
    totals = Totals()
    for call in calls:
        if call.sidechain:
            totals.sidechain_calls += 1
            continue
        if call.synthetic:
            totals.synthetic_calls += 1
            continue
        totals.calls += 1
        if call.model is not None:
            totals.models[call.model] += 1
        if call.stop_reason == "tool_use":
            totals.tool_use_stops += 1
        totals.tools.update(call.tool_uses.values())
        usage = call.usage
        if usage is None:
            totals.calls_unknown_usage += 1
            continue
        totals.input += usage.input
        totals.output += usage.output
        if usage.thinking is None:
            totals.thinking_unreported += 1
        else:
            totals.thinking += usage.thinking
        totals.cache_write_5m += usage.cache_write_5m
        totals.cache_write_1h += usage.cache_write_1h
        totals.cache_write_unspecified += usage.cache_write_unspecified
        totals.cache_read += usage.cache_read
        totals.largest_request_input = max(
            totals.largest_request_input, usage.request_input
        )
    return totals
