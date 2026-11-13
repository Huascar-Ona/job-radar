"""H1-3 and H1-4: local mapping, boundary suggestions, outcomes and reports."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.support.claude_transcripts import (
    CANARY,
    SESSION_ID,
    TranscriptBuilder,
    tool_block,
    usage,
)
from tools.harness_metrics.boundaries import Commit, suggest
from tools.harness_metrics.config import ConfigError, parse_config
from tools.harness_metrics.outcomes import (
    OutcomeError,
    parse_outcome,
    summarize,
)
from tools.harness_metrics.report import Gap, milestone_reports, snapshot
from tools.harness_metrics.transcript import Session, read_session

REPO = Path("/repo")


def config_data(*segments: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "transcripts_dir": "/transcripts",
        "sessions": [{"alias": "s1", "id": SESSION_ID}],
        "segments": list(segments),
    }


def segment(
    milestone: str, first: int, last: int, boundary: str = "inferred"
) -> dict[str, object]:
    return {
        "milestone": milestone,
        "session": "s1",
        "from_turn": first,
        "to_turn": last,
        "boundary": boundary,
        "basis": "manual",
    }


def three_turns(tmp_path: Path) -> Session:
    b = TranscriptBuilder()
    b.prompt().assistant("m1", blocks=[tool_block("t1")]).turn_duration(60_000)
    b.prompt().assistant("m2").assistant("m3").turn_duration(30_000)
    b.compaction()
    b.prompt().assistant("m4", usage_fields=usage(output=7, thinking=None))
    return read_session(b.write(tmp_path))


# --- local configuration ------------------------------------------------------


def test_a_valid_configuration_maps_milestones_in_order() -> None:
    config = parse_config(
        config_data(segment("A", 1, 2), segment("B", 3, 3)), repository=REPO
    )

    assert config.milestones() == ["A", "B"]
    assert config.transcript("s1") == Path("/transcripts") / f"{SESSION_ID}.jsonl"
    assert config.alias_of(SESSION_ID) == "s1"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        (config_data(segment("A", 1, 3), segment("B", 3, 4)), "overlap"),
        (config_data(segment("A", 4, 2)), "ends before"),
        (config_data(segment("A", -1, 2)), "non-negative"),
        (config_data(segment("bad id!", 1, 1)), "milestone"),
        (config_data({**segment("A", 1, 1), "boundary": "final"}), "boundary"),
        (config_data({**segment("A", 1, 1), "session": "s9"}), "unknown alias"),
        (config_data({**segment("A", 1, 1), "extra": 1}), "exactly"),
    ],
)
def test_invalid_configurations_are_rejected(
    data: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data, repository=REPO)


def test_configuration_errors_never_quote_the_session_id() -> None:
    data = config_data()
    data["sessions"] = [{"alias": "s1", "id": f"{SESSION_ID}/../{CANARY}"}]

    with pytest.raises(ConfigError) as caught:
        parse_config(data, repository=REPO)

    assert SESSION_ID not in str(caught.value)
    assert CANARY not in str(caught.value)


def test_the_default_transcripts_dir_follows_claude_codes_layout() -> None:
    config = parse_config(
        {"schema_version": 1, "sessions": [], "segments": []},
        repository=Path("/Users/someone/project"),
    )

    assert config.transcripts_dir.parts[-3:] == (
        ".claude",
        "projects",
        "-Users-someone-project",
    )


# --- boundary suggestions -----------------------------------------------------


def test_a_commit_suggests_the_turn_it_was_made_in(tmp_path: Path) -> None:
    session = three_turns(tmp_path)
    second = session.turns[1].started_at
    assert second is not None
    commits = [
        Commit("aaa", second + timedelta(seconds=5), "inside turn 2"),
        Commit("bbb", second - timedelta(days=1), "before the session"),
        Commit("ccc", second + timedelta(days=1), "after the session"),
    ]

    suggestions = suggest(session, commits)

    assert [(s.commit.short_sha, s.turn) for s in suggestions] == [("aaa", 2)]


# --- outcomes ledger ----------------------------------------------------------


def gate(gate_name: str, attempt: int, status: str) -> dict[str, object]:
    return {
        "milestone": "A",
        "kind": "gate_run",
        "gate": gate_name,
        "attempt": attempt,
        "status": status,
        "source": "manual",
    }


def test_outcomes_summarize_quality_without_tokens() -> None:
    entries = [
        parse_outcome(gate("unit", 1, "pass")),
        parse_outcome(gate("doc_impact", 1, "fail")),
        parse_outcome(gate("integration", 1, "blocked")),  # not decisive
        parse_outcome(gate("doc_impact", 2, "pass")),
        parse_outcome(
            {
                "milestone": "A",
                "kind": "integration_authorization",
                "status": "consumed",
                "source": "manual",
            }
        ),
        parse_outcome(
            {
                "milestone": "A",
                "kind": "intervention",
                "code": "correction",
                "count": 2,
                "source": "manual",
            }
        ),
        parse_outcome(gate("unit", 1, "fail") | {"milestone": "B"}),
    ]

    summary = summarize(entries, "A")

    assert summary.first_pass_yield == pytest.approx(0.5)
    assert summary.gate_failures == 1
    assert summary.later_runs == 1
    assert summary.failures_rerun == 1
    assert summary.sources == {"manual"}
    assert dict(summary.failures_by_gate) == {"doc_impact": 1}
    assert summary.integration_authorizations == 1
    assert dict(summary.interventions) == {"correction": 2}
    assert summary.escaped_defects is None, "unknown until checked"


def test_later_runs_are_not_all_retries() -> None:
    # Routine re-verification is a later run, not a retry. A failure counts
    # once as re-run if its gate ran again; one never re-run does not count.
    entries = [
        parse_outcome(gate("unit", 1, "pass")),
        parse_outcome(gate("unit", 2, "pass")),
        parse_outcome(gate("lint", 1, "fail")),
        parse_outcome(gate("lint", 2, "pass")),
        parse_outcome(gate("lint", 3, "pass")),
        parse_outcome(gate("format", 1, "fail")),
    ]

    summary = summarize(entries, "A")

    assert summary.later_runs == 3
    assert summary.gate_failures == 2
    assert summary.failures_rerun == 1


def test_an_escaped_defect_check_may_record_zero() -> None:
    entry = parse_outcome(
        {"milestone": "A", "kind": "escaped_defect", "count": 0, "source": "manual"}
    )

    assert summarize([entry], "A").escaped_defects == 0


@pytest.mark.parametrize(
    "raw",
    [
        gate("unit", 0, "pass"),
        gate("security", 1, "pass"),
        gate("unit", 1, "green"),
        gate("unit", 1, "pass") | {"note": f"free text {CANARY}"},
        {
            "milestone": "A",
            "kind": "intervention",
            "code": "vibes",
            "count": 1,
            "source": "manual",
        },
        {
            "milestone": "A",
            "kind": "intervention",
            "code": "correction",
            "count": 0,
            "source": "manual",
        },
        {"milestone": "A", "kind": "opinion", "source": "manual"},
    ],
)
def test_the_ledger_admits_codes_and_counts_only(raw: dict[str, object]) -> None:
    with pytest.raises(OutcomeError) as caught:
        parse_outcome(raw, 7)

    assert "line 7" in str(caught.value)
    assert CANARY not in str(caught.value)


# --- milestone reports and snapshots ------------------------------------------


def test_reports_split_a_session_by_segment(tmp_path: Path) -> None:
    session = three_turns(tmp_path)
    config = parse_config(
        config_data(segment("A", 1, 1), segment("B", 2, 3, "reviewed")),
        repository=REPO,
    )

    a, b = milestone_reports(config, {"s1": session}, pricing=None, outcomes=[])

    assert (a.totals.calls, b.totals.calls) == (1, 3)
    assert b.totals.output == 207
    assert (a.active_ms, b.active_ms) == (60_000, 30_000)
    assert b.turns_without_duration == 1
    assert b.compactions == 1
    assert a.boundary == "inferred"
    assert b.boundary == "reviewed"
    assert Gap.BOUNDARY in a.gaps
    assert Gap.BOUNDARY not in b.gaps
    assert Gap.TURN_DURATION in b.gaps
    assert Gap.COMPACTION_COST in b.gaps
    assert Gap.BILLING in a.gaps
    assert Gap.PRICING in a.gaps


def test_a_segment_beyond_the_recorded_turns_is_flagged(tmp_path: Path) -> None:
    session = three_turns(tmp_path)
    config = parse_config(
        config_data(segment("A", 1, 2), segment("B", 3, 99)), repository=REPO
    )

    a, b = milestone_reports(config, {"s1": session}, pricing=None, outcomes=[])

    assert Gap.OPEN_SEGMENT not in a.gaps
    assert Gap.OPEN_SEGMENT in b.gaps


def test_a_missing_transcript_is_a_gap_not_zero() -> None:
    config = parse_config(config_data(segment("A", 1, 2)), repository=REPO)

    [report] = milestone_reports(config, {"s1": None}, pricing=None, outcomes=[])

    assert Gap.TRANSCRIPT in report.gaps
    assert report.sessions == 0


def test_snapshots_carry_no_identifier_path_or_content(tmp_path: Path) -> None:
    b = TranscriptBuilder().private_records()
    b.prompt().assistant("m1", blocks=[tool_block("t1", "mcp__private__x")])
    b.tool_result().turn_duration(1000)
    session = read_session(b.write(tmp_path))
    config = parse_config(config_data(segment("A", 1, 1)), repository=REPO)
    [report] = milestone_reports(config, {"s1": session}, pricing=None, outcomes=[])

    text = json.dumps(snapshot(report))

    for private in (CANARY, SESSION_ID, "s1", "/Users", "/transcripts", "mcp__"):
        assert private not in text
    data = json.loads(text)
    assert data["milestone"] == "A"
    assert data["activity"]["tool_calls"] == {"other": 1}
    assert data["cost"] is None
    assert data["quality"]["escaped_defects"] is None
