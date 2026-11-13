"""The harness-metrics CLI, end to end on synthetic transcripts.

Privacy is checked on every output: nothing from a content-bearing field
(``CANARY``), no real session id and no transcript path may appear unless
``--show-ids`` asks for the id.
"""

import io
import json
from pathlib import Path

import pytest

from tests.support.claude_transcripts import (
    CANARY,
    SESSION_ID,
    TranscriptBuilder,
    tool_block,
)
from tools.harness_metrics.cli import main

PRICING = """\
schema_version = 1
version = "test-rates-1"
currency = "USD"
unit = "per_million_tokens"
source = "invented for tests"
retrieved = 2026-11-12

[[rates]]
model = "claude-opus-5-5"
service_tier = "standard"
speed = "standard"
inference_geo = "global"
min_request_input_tokens = 0
max_request_input_tokens = "none"
input = "1"
output = "1"
cache_write_5m = "1"
cache_write_1h = "1"
cache_read = "1"
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    b = TranscriptBuilder().private_records()
    b.prompt().assistant("m1", blocks=[tool_block("t1")]).tool_result()
    b.turn_duration(2000)
    b.prompt().assistant("m2").turn_duration(1000)
    b.write(transcripts)
    (tmp_path / "metrics.local.toml").write_text(
        f'schema_version = 1\ntranscripts_dir = "{transcripts}"\n'
        f'[[sessions]]\nalias = "s1"\nid = "{SESSION_ID}"\n'
        '[[segments]]\nmilestone = "M1"\nsession = "s1"\nfrom_turn = 1\n'
        'to_turn = 2\nboundary = "reviewed"\nbasis = "manual"\n',
        encoding="utf-8",
    )
    (tmp_path / "pricing.toml").write_text(PRICING, encoding="utf-8")
    (tmp_path / "outcomes.jsonl").write_text(
        '{"milestone": "M1", "kind": "gate_run", "gate": "unit", "attempt": 1,'
        ' "status": "pass", "source": "manual"}\n',
        encoding="utf-8",
    )
    return tmp_path


def run(workspace: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(
        [
            "--repository",
            str(workspace),
            "--config",
            str(workspace / "metrics.local.toml"),
            *argv,
        ],
        stdout=out,
    )
    return code, out.getvalue()


def assert_private(text: str, workspace: Path) -> None:
    assert CANARY not in text
    assert SESSION_ID not in text
    assert str(workspace) not in text


@pytest.mark.parametrize(
    "argv",
    [
        ("sessions",),
        ("sessions", "--json"),
        ("session", "s1"),
        ("session", "s1", "--json"),
        ("milestones", "--pricing", "pricing.toml", "--outcomes", "outcomes.jsonl"),
        ("milestones", "--json", "--pricing", "pricing.toml"),
        ("validate", "--pricing", "pricing.toml", "--outcomes", "outcomes.jsonl"),
    ],
)
def test_no_output_exposes_content_ids_or_paths(
    workspace: Path, argv: tuple[str, ...]
) -> None:
    code, text = run(workspace, *argv)

    assert code == 0
    assert text
    assert_private(text, workspace)


def test_session_ids_appear_only_on_request(workspace: Path) -> None:
    _, hidden = run(workspace, "sessions", "--json")
    _, shown = run(workspace, "sessions", "--json", "--show-ids")

    assert SESSION_ID not in hidden
    assert json.loads(shown)[0]["session_id"] == SESSION_ID
    assert CANARY not in shown


def test_milestone_report_prices_and_labels_the_estimate(workspace: Path) -> None:
    code, text = run(
        workspace,
        "milestones",
        "--json",
        "--pricing",
        "pricing.toml",
        "--outcomes",
        "outcomes.jsonl",
    )

    [data] = json.loads(text)
    assert code == 0
    assert data["cost"]["label"] == "estimate, not billing"
    # Two calls of (3 + 100 + 50 + 1000) tokens at 1 per million tokens.
    assert data["cost"]["amount"] == "0.0023"
    assert data["quality"]["first_decisive_runs_passed"] == 1
    assert data["quality"]["first_decisive_runs"] == 1
    assert data["quality"]["sources"] == ["manual"]
    assert data["boundary"]["status"] == "reviewed"
    assert data["time"]["active_agent_ms"] == 3000
    assert "billing_not_recorded_locally" in data["data_gaps"]


def test_snapshots_are_written_sanitized(workspace: Path) -> None:
    target = workspace / "snapshots"

    code, _ = run(
        workspace,
        "milestones",
        "--pricing",
        "pricing.toml",
        "--write-snapshots",
        str(target),
        "--only",
        "M1",
    )

    written = (target / "M1.json").read_text(encoding="utf-8")
    assert code == 0
    assert json.loads(written)["milestone"] == "M1"
    assert_private(written, workspace)


def test_an_unknown_milestone_filter_is_an_error(workspace: Path) -> None:
    code, _ = run(workspace, "milestones", "--only", "nope")

    assert code == 1


def test_strict_mode_fails_on_anomalies(workspace: Path) -> None:
    transcript = workspace / "transcripts" / f"{SESSION_ID}.jsonl"
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    relaxed, _ = run(workspace, "sessions")
    strict, _ = run(workspace, "sessions", "--strict")

    assert (relaxed, strict) == (0, 1)


def test_invalid_pricing_exits_one_without_quoting_it(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace / "pricing.toml").write_text(f"version = {CANARY}\n", encoding="utf-8")

    code, _ = run(workspace, "validate", "--pricing", "pricing.toml")

    assert code == 1
    assert CANARY not in capsys.readouterr().err


def test_a_missing_local_configuration_is_reported(workspace: Path) -> None:
    out = io.StringIO()

    code = main(
        [
            "--repository",
            str(workspace),
            "--config",
            str(workspace / "absent.toml"),
            "milestones",
        ],
        stdout=out,
    )

    assert code == 1
