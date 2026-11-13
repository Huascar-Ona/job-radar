"""H1-1: transcript parsing, deduplication, turns and aggregation."""

from pathlib import Path

import pytest

from tests.support.claude_transcripts import (
    CANARY,
    TranscriptBuilder,
    text_block,
    thinking_block,
    tool_block,
    usage,
)
from tools.harness_metrics.aggregate import total
from tools.harness_metrics.transcript import Anomaly, Session, read_session


def parse(builder: TranscriptBuilder, tmp_path: Path) -> Session:
    return read_session(builder.write(tmp_path))


# --- deduplication -----------------------------------------------------------------


def test_each_message_is_counted_once_across_its_block_lines(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant("msg_1", blocks=[thinking_block(), text_block(), tool_block("tu_1")])
    b.assistant("msg_2", blocks=[text_block()])

    session = parse(b, tmp_path)
    totals = total(session.calls)

    assert totals.calls == 2
    assert totals.output == 200
    assert totals.cache_read == 2000
    assert totals.thinking == 80
    assert totals.complete


def test_conflicting_duplicates_make_usage_unknown_not_guessed(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant("msg_1", blocks=[text_block()], usage_fields=usage(output=100))
    b.assistant("msg_1", blocks=[text_block()], usage_fields=usage(output=999))
    b.assistant("msg_2")

    session = parse(b, tmp_path)
    totals = total(session.calls)

    assert session.anomalies[Anomaly.CONFLICTING_DUPLICATE] == 1
    assert totals.calls == 2
    assert totals.calls_unknown_usage == 1
    assert totals.output == 100, "only msg_2 is summed"
    assert not totals.complete


@pytest.mark.parametrize(
    "bad",
    [
        {"input_tokens": 1, "output_tokens": 2},  # missing fields
        usage(extra={"output_tokens": "12"}),  # not an integer
        usage(extra={"cache_read_input_tokens": -1}),  # negative
        usage(extra={"input_tokens": True}),  # boolean
        usage(output=10, thinking=11),  # thinking cannot exceed output
        usage(extra={"cache_creation_input_tokens": 10}),  # split exceeds total
    ],
)
def test_incomplete_or_inconsistent_usage_is_unknown(
    tmp_path: Path, bad: dict[str, object]
) -> None:
    b = TranscriptBuilder().prompt().assistant("msg_1", usage_fields=bad)

    session = parse(b, tmp_path)
    totals = total(session.calls)

    assert totals.calls_unknown_usage == 1
    assert totals.processed_input == 0
    assert sum(session.anomalies.values()) == 1


def test_iterations_are_never_added_and_more_than_one_is_reported(
    tmp_path: Path,
) -> None:
    doubled = usage()
    doubled["iterations"] = [doubled["iterations"][0]] * 2  # type: ignore[index]
    b = TranscriptBuilder().prompt().assistant("msg_1", usage_fields=doubled)

    session = parse(b, tmp_path)

    assert total(session.calls).output == 100
    assert session.anomalies[Anomaly.MULTIPLE_ITERATIONS] == 1


def test_messages_are_scoped_to_their_session(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant("msg_1", session_id="99999999-0000-0000-0000-000000000000")

    session = parse(b, tmp_path)

    assert session.calls == []
    assert session.anomalies[Anomaly.FOREIGN_SESSION] == 1


# --- token categories ---------------------------------------------------------------


def test_categories_stay_apart_and_derived_quantities_are_named(
    tmp_path: Path,
) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant(
        "msg_1",
        usage_fields=usage(
            input_tokens=5,
            output=70,
            cache_write_1h=30,
            cache_write_5m=20,
            cache_read=900,
            thinking=10,
        ),
    )
    b.assistant(
        "msg_2",
        usage_fields=usage(
            input_tokens=1, output=30, cache_write_1h=0, cache_read=1200, thinking=None
        ),
    )

    totals = total(parse(b, tmp_path).calls)

    assert (totals.input, totals.output, totals.thinking) == (6, 100, 10)
    assert (totals.cache_write_5m, totals.cache_write_1h) == (20, 30)
    assert totals.cache_read == 2100
    assert totals.thinking_unreported == 1
    assert totals.processed_input == 6 + 50 + 2100
    assert totals.new_context == 56
    assert totals.generated == 100
    assert totals.largest_request_input == 1 + 0 + 1200
    assert totals.cache_read_share == pytest.approx(2100 / 2156)


def test_cache_writes_without_a_lifetime_are_kept_apart(tmp_path: Path) -> None:
    unsplit = usage(cache_write_1h=0)
    unsplit["cache_creation_input_tokens"] = 40
    del unsplit["cache_creation"]
    b = TranscriptBuilder().prompt().assistant("msg_1", usage_fields=unsplit)

    totals = total(parse(b, tmp_path).calls)

    assert totals.cache_write_unspecified == 40
    assert totals.cache_write == 40


def test_synthetic_and_sidechain_calls_are_excluded_and_counted(
    tmp_path: Path,
) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant("msg_1")
    b.assistant("msg_2", model="<synthetic>")
    b.assistant("msg_3", sidechain=True)

    totals = total(parse(b, tmp_path).calls)

    assert (totals.calls, totals.synthetic_calls, totals.sidechain_calls) == (1, 1, 1)
    assert totals.output == 100


def test_unknown_usage_keys_are_reported_as_drift(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant("msg_1", usage_fields=usage(extra={"new_meter": 5}))

    session = parse(b, tmp_path)

    assert session.unknown_usage_keys["new_meter"] == 1
    assert total(session.calls).complete


# --- turns, time and tools ------------------------------------------------------------


def test_turns_start_at_typed_prompts_and_carry_active_time(tmp_path: Path) -> None:
    b = TranscriptBuilder().private_records()
    b.prompt().assistant("msg_1").tool_result().assistant("msg_2").turn_duration(4000)
    b.compaction()
    b.prompt().assistant("msg_3")

    session = parse(b, tmp_path)

    assert [t.index for t in session.turns] == [1, 2]
    first, second = session.turns
    assert [c.message_id for c in first.calls] == ["msg_1", "msg_2"]
    assert first.active_ms == 4000
    assert first.compactions == 1
    assert second.active_ms is None, "a turn still running has no duration yet"
    assert session.first_at is not None
    assert session.last_at is not None
    assert session.first_at < session.last_at


def test_bad_turn_durations_are_anomalies(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt().assistant("msg_1").turn_duration("12")
    b.turn_duration(-5)

    session = parse(b, tmp_path)

    assert session.turns[0].active_ms is None
    assert session.anomalies[Anomaly.BAD_DURATION] == 2


def test_tool_names_are_allowlisted_and_deduplicated(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.assistant(
        "msg_1",
        blocks=[
            tool_block("tu_1", "Bash"),
            tool_block("tu_2", "mcp__private-server__secret_tool"),
            tool_block("tu_1", "Bash"),  # the same block seen again
        ],
    )

    totals = total(parse(b, tmp_path).calls)

    assert dict(totals.tools) == {"Bash": 1, "other": 1}
    assert totals.tool_use_stops == 1


def test_malformed_lines_and_unknown_record_types_are_counted(tmp_path: Path) -> None:
    b = TranscriptBuilder().raw("{not json").raw("[1, 2]")
    b.record(type="brand-new-type", payload=CANARY)
    b.prompt().assistant("msg_1")

    session = parse(b, tmp_path)

    assert session.anomalies[Anomaly.MALFORMED_LINE] == 2
    assert session.unknown_record_types["brand-new-type"] == 1
    assert total(session.calls).calls == 1


def test_assistant_records_without_an_id_are_anomalies(tmp_path: Path) -> None:
    b = TranscriptBuilder().prompt()
    b.record(type="assistant", sessionId=b.session_id, message={"usage": usage()})

    session = parse(b, tmp_path)

    assert session.calls == []
    assert session.anomalies[Anomaly.ASSISTANT_WITHOUT_ID] == 1


def test_parsing_never_modifies_the_transcript(tmp_path: Path) -> None:
    path = TranscriptBuilder().prompt().assistant("msg_1").write(tmp_path)
    before = path.read_bytes()

    read_session(path)

    assert path.read_bytes() == before
