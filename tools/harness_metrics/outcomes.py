"""The ledger of verification outcomes and human interventions (D6).

Committed as ``docs/metrics/outcomes.jsonl``, one JSON object per line. The
schema admits enumerated codes and counts only, no free text, so the ledger
cannot carry prompts, output or identifiers::

    {"milestone": "4G", "kind": "gate_run", "gate": "unit", "attempt": 1,
     "status": "pass", "source": "backfill"}
    {"milestone": "4G", "kind": "intervention", "code": "contract_amendment",
     "count": 2, "source": "backfill"}

These are quality signals. They sit beside token figures and are never
derived from them: fewer tokens is not better engineering.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

GATES: Final = (
    "lint",
    "format",
    "typecheck",
    "unit",
    "integration",
    "doc_impact",
    "diff_check",
)
GATE_STATUSES: Final = ("pass", "fail", "blocked", "unverified", "skipped")
#: Statuses that say whether a gate's check succeeded.
DECISIVE: Final = ("pass", "fail")
INTERVENTIONS: Final = (
    # The maintainer changed an approved or proposed contract decision.
    "contract_amendment",
    # The maintainer narrowed, widened or redirected the requested work.
    "scope_change",
    # The maintainer corrected a mistake the agent made or was about to make.
    "correction",
    # The maintainer rejected a proposal outright.
    "rejected_proposal",
    # The agent stopped to ask for a decision it could not take itself.
    "decision_request",
)
SOURCES: Final = ("manual", "backfill", "verify-change")
_MILESTONE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_FIELDS: Final = {
    "gate_run": {"milestone", "kind", "gate", "attempt", "status", "source"},
    "integration_authorization": {"milestone", "kind", "status", "source"},
    "intervention": {"milestone", "kind", "code", "count", "source"},
    "escaped_defect": {"milestone", "kind", "count", "source"},
}


class OutcomeError(ValueError):
    """A ledger line is invalid. The message gives its line number only."""


@dataclass(frozen=True, slots=True)
class Outcome:
    milestone: str
    kind: str
    source: str
    gate: str | None = None
    attempt: int | None = None
    status: str | None = None
    code: str | None = None
    count: int = 1


@dataclass(slots=True)
class OutcomeSummary:
    """What the ledger says about one milestone.

    ``later_runs`` counts every run of a gate after its first, for any
    reason: most are routine re-verification after an unrelated change.
    ``failures_rerun`` counts failures that were followed by a later run of
    the same gate, each failure once: the closest the ledger gets to "fixed
    and checked again". Neither says why a run happened; the ledger does not
    record that.
    """

    recorded: bool = False
    sources: set[str] = field(default_factory=set)
    gate_runs: int = 0
    gate_failures: int = 0
    later_runs: int = 0
    failures_rerun: int = 0
    first_attempts: int = 0
    first_attempt_passes: int = 0
    failures_by_gate: Counter[str] = field(default_factory=Counter)
    integration_authorizations: int = 0
    interventions: Counter[str] = field(default_factory=Counter)
    #: ``None`` until someone records a check; 0 means checked, none found.
    escaped_defects: int | None = None

    @property
    def first_pass_yield(self) -> float | None:
        """Passes among first decisive runs (pass or fail) of each gate.

        Blocked, skipped and unverified runs say nothing about quality.
        """
        if not self.first_attempts:
            return None
        return self.first_attempt_passes / self.first_attempts


def load_outcomes(path: Path) -> list[Outcome]:
    if not path.exists():
        return []
    outcomes = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            msg = f"outcomes line {number} is not JSON"
            raise OutcomeError(msg) from None
        outcomes.append(parse_outcome(raw, number))
    return outcomes


def parse_outcome(raw: object, number: int = 0) -> Outcome:
    where = f"outcomes line {number}"
    if not isinstance(raw, dict):
        msg = f"{where} must be an object"
        raise OutcomeError(msg)
    kind = raw.get("kind")
    if kind not in _FIELDS:
        msg = f"{where}: kind must be one of {', '.join(_FIELDS)}"
        raise OutcomeError(msg)
    expected = _FIELDS[str(kind)]
    if set(raw) != expected:
        msg = f"{where}: {kind} must have exactly: {', '.join(sorted(expected))}"
        raise OutcomeError(msg)
    milestone = raw["milestone"]
    if not isinstance(milestone, str) or not _MILESTONE.fullmatch(milestone):
        msg = f"{where}: milestone must match {_MILESTONE.pattern}"
        raise OutcomeError(msg)
    source = raw["source"]
    if source not in SOURCES:
        msg = f"{where}: source must be one of {', '.join(SOURCES)}"
        raise OutcomeError(msg)
    if kind == "gate_run":
        gate, status = raw["gate"], raw["status"]
        attempt = _positive(raw["attempt"], f"{where}: attempt")
        if gate not in GATES:
            msg = f"{where}: gate must be one of {', '.join(GATES)}"
            raise OutcomeError(msg)
        if status not in GATE_STATUSES:
            msg = f"{where}: status must be one of {', '.join(GATE_STATUSES)}"
            raise OutcomeError(msg)
        return Outcome(
            milestone,
            "gate_run",
            str(source),
            gate=str(gate),
            attempt=attempt,
            status=str(status),
        )
    if kind == "integration_authorization":
        if raw["status"] not in ("consumed", "unused"):
            msg = f"{where}: status must be consumed or unused"
            raise OutcomeError(msg)
        return Outcome(milestone, str(kind), str(source), status=str(raw["status"]))
    if kind == "escaped_defect":
        # 0 is meaningful here: the milestone was checked and none was found.
        count = raw["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            msg = f"{where}: count must be a non-negative integer"
            raise OutcomeError(msg)
        return Outcome(milestone, str(kind), str(source), count=count)
    count = _positive(raw["count"], f"{where}: count")
    if raw["code"] not in INTERVENTIONS:
        msg = f"{where}: code must be one of {', '.join(INTERVENTIONS)}"
        raise OutcomeError(msg)
    return Outcome(
        milestone, str(kind), str(source), code=str(raw["code"]), count=count
    )


def summarize(outcomes: Iterable[Outcome], milestone: str) -> OutcomeSummary:
    summary = OutcomeSummary()
    entries = [o for o in outcomes if o.milestone == milestone]
    attempts: dict[str, list[int]] = {}
    for outcome in entries:
        if outcome.kind == "gate_run":
            attempts.setdefault(str(outcome.gate), []).append(outcome.attempt or 0)
    for outcome in entries:
        summary.recorded = True
        summary.sources.add(outcome.source)
        if outcome.kind == "gate_run":
            summary.gate_runs += 1
            attempt = outcome.attempt or 0
            if outcome.status == "fail":
                summary.gate_failures += 1
                summary.failures_by_gate[str(outcome.gate)] += 1
                if any(a > attempt for a in attempts[str(outcome.gate)]):
                    summary.failures_rerun += 1
            if attempt == 1 and outcome.status in DECISIVE:
                summary.first_attempts += 1
                summary.first_attempt_passes += outcome.status == "pass"
            elif attempt > 1:
                summary.later_runs += 1
        elif outcome.kind == "integration_authorization":
            summary.integration_authorizations += outcome.status == "consumed"
        elif outcome.kind == "intervention":
            summary.interventions[str(outcome.code)] += outcome.count
        elif outcome.kind == "escaped_defect":
            summary.escaped_defects = (summary.escaped_defects or 0) + outcome.count
    return summary


def _positive(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = f"{what} must be a positive integer"
        raise OutcomeError(msg)
    return value
