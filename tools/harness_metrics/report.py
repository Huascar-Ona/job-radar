"""Per-milestone reports and the sanitized snapshots that may be committed.

A snapshot names the milestone only: no session id, alias, path, branch,
account identifier or content (D8). Every figure says what it is; values
that are not known are ``null`` with a reason in ``data_gaps``, never 0.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from tools.harness_metrics.aggregate import Totals, total
from tools.harness_metrics.config import LocalConfig
from tools.harness_metrics.outcomes import Outcome, OutcomeSummary, summarize
from tools.harness_metrics.pricing import Estimate, PricingTable, estimate
from tools.harness_metrics.transcript import ApiCall, Session

SNAPSHOT_SCHEMA: Final = 1


class Gap:
    """Codes for data a report cannot provide."""

    BILLING: Final = "billing_not_recorded_locally"
    COMPACTION_COST: Final = "compaction_call_usage_not_recorded"
    TURN_DURATION: Final = "turn_duration_missing"
    PRICING: Final = "cost_estimate_unavailable"
    OUTCOMES: Final = "outcomes_not_recorded"
    USAGE: Final = "usage_unknown_for_some_calls"
    BOUNDARY: Final = "boundary_inferred_not_reviewed"
    TRANSCRIPT: Final = "transcript_missing"
    ESCAPED_DEFECTS: Final = "escaped_defects_not_checked"
    OPEN_SEGMENT: Final = "segment_extends_beyond_recorded_turns"


@dataclass(slots=True)
class MilestoneReport:
    milestone: str
    boundary: str
    bases: list[str]
    sessions: int
    typed_turns: int
    totals: Totals
    active_ms: int
    turns_without_duration: int
    wall_clock_ms: int
    compactions: int
    estimate: Estimate | None
    outcomes: OutcomeSummary
    session_anomalies: Counter[str] = field(default_factory=Counter)
    as_of: str | None = None
    gaps: list[str] = field(default_factory=list)


def milestone_reports(
    config: LocalConfig,
    sessions: dict[str, Session | None],
    *,
    pricing: PricingTable | None,
    outcomes: list[Outcome],
) -> list[MilestoneReport]:
    """One report per milestone in ``config``, in first-mention order.

    ``sessions`` maps alias to the parsed session, or ``None`` when its
    transcript is no longer on disk.
    """
    reports = []
    for milestone in config.milestones():
        segments = [s for s in config.segments if s.milestone == milestone]
        calls: dict[tuple[str, str], ApiCall] = {}
        typed = active = missing_duration = wall = compactions = 0
        anomalies: Counter[str] = Counter()
        last_stamp = None
        transcript_missing = False
        open_segment = False
        used_sessions = set()
        for segment in segments:
            session = sessions.get(segment.session)
            if session is None:
                transcript_missing = True
                continue
            used_sessions.add(segment.session)
            anomalies.update(session.anomalies)
            if segment.to_turn > max((t.index for t in session.turns), default=0):
                # The range claims turns that do not exist yet: later turns
                # would be attributed silently. Reported, never assumed.
                open_segment = True
            turns = [t for t in session.turns if segment.covers(t.index)]
            for turn in turns:
                typed += turn.typed
                compactions += turn.compactions
                if turn.active_ms is None:
                    missing_duration += 1
                else:
                    active += turn.active_ms
                for call in turn.calls:
                    # One count per (session, message), across segments too.
                    calls.setdefault((segment.session, call.message_id), call)
            starts = [t.started_at for t in turns if t.started_at is not None]
            ends = [t.last_at for t in turns if t.last_at is not None]
            if starts and ends:
                wall += int((max(ends) - min(starts)).total_seconds() * 1000)
                if last_stamp is None or max(ends) > last_stamp:
                    last_stamp = max(ends)

        totals = total(calls.values())
        cost = estimate(calls.values(), pricing)
        summary = summarize(outcomes, milestone)
        boundary = (
            "reviewed"
            if all(s.boundary == "reviewed" for s in segments)
            else "inferred"
        )
        gaps = [Gap.BILLING]
        if compactions:
            gaps.append(Gap.COMPACTION_COST)
        if missing_duration:
            gaps.append(Gap.TURN_DURATION)
        if cost is None or cost.amount is None:
            gaps.append(Gap.PRICING)
        if not summary.recorded:
            gaps.append(Gap.OUTCOMES)
        if summary.escaped_defects is None:
            gaps.append(Gap.ESCAPED_DEFECTS)
        if not totals.complete:
            gaps.append(Gap.USAGE)
        if boundary != "reviewed":
            gaps.append(Gap.BOUNDARY)
        if transcript_missing:
            gaps.append(Gap.TRANSCRIPT)
        if open_segment:
            gaps.append(Gap.OPEN_SEGMENT)
        reports.append(
            MilestoneReport(
                milestone=milestone,
                boundary=boundary,
                bases=sorted({s.basis for s in segments}),
                sessions=len(used_sessions),
                typed_turns=typed,
                totals=totals,
                active_ms=active,
                turns_without_duration=missing_duration,
                wall_clock_ms=wall,
                compactions=compactions,
                estimate=cost,
                outcomes=summary,
                session_anomalies=anomalies,
                as_of=last_stamp.isoformat() if last_stamp else None,
                gaps=gaps,
            )
        )
    return reports


def snapshot(report: MilestoneReport) -> dict[str, object]:
    """The committed form of a report: aggregates and codes only."""
    t = report.totals
    cost = report.estimate
    o = report.outcomes
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "milestone": report.milestone,
        "as_of": report.as_of,
        "boundary": {"status": report.boundary, "basis": report.bases},
        "scope": {
            "sessions": report.sessions,
            "typed_turns": report.typed_turns,
            "api_calls": t.calls,
            "api_calls_unknown_usage": t.calls_unknown_usage,
            "excluded_synthetic_calls": t.synthetic_calls,
            "excluded_subagent_calls": t.sidechain_calls,
        },
        "tokens": {
            "input_uncached": t.input,
            "output": t.output,
            "output_thinking_subset": t.thinking,
            "calls_without_thinking_count": t.thinking_unreported,
            "cache_write_5m": t.cache_write_5m,
            "cache_write_1h": t.cache_write_1h,
            "cache_write_unspecified": t.cache_write_unspecified,
            "cache_read": t.cache_read,
            "processed_input_cumulative": t.processed_input,
            "new_context": t.new_context,
            "generated_output": t.generated,
            "largest_request_input": t.largest_request_input,
            "cache_read_share": _ratio(t.cache_read_share),
            "complete": t.complete,
        },
        "activity": {
            "tool_use_stops": t.tool_use_stops,
            "tool_calls": dict(sorted(t.tools.items())),
            "models": dict(sorted(t.models.items())),
            "compactions": report.compactions,
        },
        "time": {
            "active_agent_ms": report.active_ms,
            "turns_without_duration": report.turns_without_duration,
            "wall_clock_span_ms": report.wall_clock_ms,
        },
        "cost": None
        if cost is None
        else {
            "label": "estimate, not billing",
            "pricing_version": cost.pricing_version,
            "currency": cost.currency,
            "amount": None if cost.amount is None else _money(cost.amount),
            "priced_calls": cost.priced_calls,
            "unpriced_calls": cost.unpriced_calls,
            "unpriced_reasons": cost.unpriced_reasons,
        },
        "quality": {
            "recorded": o.recorded,
            "sources": sorted(o.sources),
            "gate_runs": o.gate_runs,
            "gate_failures": o.gate_failures,
            "failures_by_gate": dict(sorted(o.failures_by_gate.items())),
            "later_runs_any_reason": o.later_runs,
            "failures_rerun": o.failures_rerun,
            "first_decisive_runs": o.first_attempts,
            "first_decisive_runs_passed": o.first_attempt_passes,
            "integration_authorizations_consumed": o.integration_authorizations,
            "interventions": dict(sorted(o.interventions.items())),
            "escaped_defects": o.escaped_defects,
        },
        "data_quality": {
            "session_anomalies": dict(sorted(report.session_anomalies.items())),
        },
        "data_gaps": report.gaps,
    }


def render_table(reports: list[MilestoneReport]) -> str:
    """A plain-text comparison, one column per milestone."""
    if not reports:
        return "No milestones are mapped in the local configuration.\n"
    rows: list[tuple[str, list[str]]] = []

    def row(label: str, values: list[object]) -> None:
        rows.append((label, ["-" if v is None else str(v) for v in values]))

    row("boundary", [r.boundary for r in reports])
    row("typed turns", [r.typed_turns for r in reports])
    row("api calls", [r.totals.calls for r in reports])
    row("input (uncached)", [r.totals.input for r in reports])
    row("output", [r.totals.output for r in reports])
    row("  of which thinking", [r.totals.thinking for r in reports])
    row("cache write 5m", [r.totals.cache_write_5m for r in reports])
    row("cache write 1h", [r.totals.cache_write_1h for r in reports])
    row("cache read", [r.totals.cache_read for r in reports])
    row("processed input (cum.)", [r.totals.processed_input for r in reports])
    row("new context", [r.totals.new_context for r in reports])
    row("largest request input", [r.totals.largest_request_input for r in reports])
    row("usage complete", [r.totals.complete for r in reports])
    row("active agent time", [_duration(r.active_ms) for r in reports])
    row("wall-clock span", [_duration(r.wall_clock_ms) for r in reports])
    row("compactions", [r.compactions for r in reports])
    row(
        "cost estimate",
        [
            "unknown"
            if r.estimate is None or r.estimate.amount is None
            else f"{_money(r.estimate.amount)} {r.estimate.currency}"
            for r in reports
        ],
    )
    row(
        "quality source",
        ["+".join(sorted(r.outcomes.sources)) or None for r in reports],
    )
    row(
        "first runs passed",
        [
            f"{r.outcomes.first_attempt_passes}/{r.outcomes.first_attempts}"
            if r.outcomes.first_attempts
            else None
            for r in reports
        ],
    )
    row("gate failures", [r.outcomes.gate_failures for r in reports])
    row("failures re-run", [r.outcomes.failures_rerun for r in reports])
    row("later runs (any reason)", [r.outcomes.later_runs for r in reports])
    row("integration runs", [r.outcomes.integration_authorizations for r in reports])
    row(
        "recorded interventions",
        [sum(r.outcomes.interventions.values()) for r in reports],
    )
    row("escaped defects", [r.outcomes.escaped_defects for r in reports])

    label_width = max(len(label) for label, _ in rows)
    widths = [
        max(len(r.milestone), *(len(values[i]) for _, values in rows))
        for i, r in enumerate(reports)
    ]
    header = (
        " " * label_width
        + "  "
        + "  ".join(r.milestone.rjust(w) for r, w in zip(reports, widths, strict=True))
    )
    lines = [header.rstrip()]
    for label, values in rows:
        lines.append(
            label.ljust(label_width)
            + "  "
            + "  ".join(v.rjust(w) for v, w in zip(values, widths, strict=True))
        )
    lines.append("")
    lines.append(
        "Cost figures are estimates from docs/metrics/pricing.toml, not billing."
    )
    lines.append(
        "Token totals are not a quality measure; read them with the quality rows."
    )
    lines.append(
        "Largest request input is the API's usage accounting for one request,"
        " not a measurement of the model's context window."
    )
    return "\n".join(lines) + "\n"


def _duration(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def _ratio(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _money(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.0001")))
