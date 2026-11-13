"""Claude Code session transcripts, read for metadata only.

Format, as observed in Claude Code 2.1.x (H1 discovery, 2026-11-12):

- One JSON object per line, appended while the session runs.
- An assistant message is written once per content block: every line of one
  ``message.id`` repeats the same ``usage``. Usage is therefore counted once
  per ``(session, message.id)``; a duplicate whose usage differs is a
  conflict, and that message's usage becomes unknown rather than guessed.
- ``usage.iterations`` repeats the top-level usage and is never added.
- ``output_tokens_details.thinking_tokens`` is part of ``output_tokens``.
- Cache writes are split by lifetime under ``usage.cache_creation``.
- A turn starts at a user record with ``promptSource == "typed"``; its active
  time is a ``system`` record with subtype ``turn_duration``.

Privacy allowlist. Only these are read: record ``type``, ``timestamp``,
``sessionId``, ``isSidechain``, ``promptSource``, ``version``; for assistant
records ``message.id``, ``message.model``, ``message.stop_reason``, the
numeric ``message.usage`` fields, ``service_tier``, ``speed``,
``inference_geo`` and, from
``tool_use`` content blocks, the block ``id`` and the tool ``name`` (counted
only if it is in ``TOOL_ALLOWLIST``, otherwise as ``other``); for system
records ``subtype`` and ``durationMs``. Text, thinking, tool inputs and
results, attachments, request bodies, file snapshots, account identifiers,
paths and branch names are never read into the model, and record types that
carry them are skipped whole.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

#: Record types that carry nothing H1 needs and may carry content or private
#: identifiers. Skipped without looking inside.
SKIPPED_TYPES: Final = frozenset(
    {
        "api-request",
        "api-request-blob",
        "api-request-shape",
        "attachment",
        "ai-title",
        "atis-latch",
        "file-history-delta",
        "file-history-snapshot",
        "last-prompt",
        "mode",
        "permission-mode",
        "session-owner",
        "summary",
        "tools-used",
    }
)
READ_TYPES: Final = frozenset({"assistant", "user", "system"})

REQUIRED_USAGE: Final = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
#: Usage keys seen during discovery. Any other key is reported as schema
#: drift; it is never silently interpreted.
KNOWN_USAGE_KEYS: Final = frozenset(
    {
        *REQUIRED_USAGE,
        "cache_creation",
        "fallback_credit",
        "inference_geo",
        "iterations",
        "output_tokens_details",
        "server_tool_use",
        "service_tier",
        "speed",
    }
)

#: Built-in Claude Code tool names that may be counted. Anything else, MCP
#: tools included (their names can reveal private servers), counts as
#: ``other``.
TOOL_ALLOWLIST: Final = frozenset(
    {
        "Agent",
        "Artifact",
        "AskUserQuestion",
        "Bash",
        "Edit",
        "Glob",
        "Grep",
        "Monitor",
        "NotebookEdit",
        "Read",
        "SendMessage",
        "Skill",
        "Task",
        "TodoWrite",
        "ToolSearch",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)
OTHER_TOOL: Final = "other"
SYNTHETIC_MODEL: Final = "<synthetic>"


class Anomaly:
    """Codes for records that could not be counted as-is."""

    MALFORMED_LINE: Final = "malformed_line"
    FOREIGN_SESSION: Final = "foreign_session_record"
    ASSISTANT_WITHOUT_ID: Final = "assistant_without_id"
    INCOMPLETE_USAGE: Final = "incomplete_usage"
    CONFLICTING_DUPLICATE: Final = "conflicting_duplicate_usage"
    MULTIPLE_ITERATIONS: Final = "multiple_usage_iterations"
    CACHE_SPLIT_EXCEEDS_TOTAL: Final = "cache_split_exceeds_total"
    BAD_TIMESTAMP: Final = "bad_timestamp"
    BAD_DURATION: Final = "bad_turn_duration"


@dataclass(frozen=True, slots=True)
class Usage:
    """One API call's tokens. ``thinking`` is part of ``output``."""

    input: int
    output: int
    cache_write_5m: int
    cache_write_1h: int
    #: Cache writes the record did not attribute to a lifetime.
    cache_write_unspecified: int
    cache_read: int
    #: ``None`` when the record does not report it.
    thinking: int | None

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h + self.cache_write_unspecified

    @property
    def request_input(self) -> int:
        """The request's input as the API accounted it: uncached input plus
        cache writes plus cache reads. Excludes the output this call produced.
        """
        return self.input + self.cache_write + self.cache_read


@dataclass(slots=True)
class ApiCall:
    """One assistant message, i.e. one API request."""

    message_id: str
    timestamp: datetime | None
    model: str | None
    service_tier: str | None
    speed: str | None
    inference_geo: str | None
    stop_reason: str | None
    #: ``None`` when unknown: incomplete, or conflicting between duplicates.
    usage: Usage | None
    sidechain: bool
    #: Tool-use block id -> counted tool name.
    tool_uses: dict[str, str] = field(default_factory=dict)
    signature: tuple[object, ...] | None = None
    conflicted: bool = False

    @property
    def synthetic(self) -> bool:
        return self.model == SYNTHETIC_MODEL


@dataclass(slots=True)
class Turn:
    """From one typed prompt to the next. Turn 0 holds anything before one."""

    index: int
    started_at: datetime | None
    typed: bool
    last_at: datetime | None = None
    calls: list[ApiCall] = field(default_factory=list)
    active_ms: int | None = None
    compactions: int = 0


@dataclass(slots=True)
class Session:
    session_id: str
    turns: list[Turn]
    client_versions: Counter[str] = field(default_factory=Counter)
    anomalies: Counter[str] = field(default_factory=Counter)
    unknown_record_types: Counter[str] = field(default_factory=Counter)
    unknown_usage_keys: Counter[str] = field(default_factory=Counter)

    @property
    def calls(self) -> list[ApiCall]:
        return [call for turn in self.turns for call in turn.calls]

    @property
    def first_at(self) -> datetime | None:
        stamps = [t.started_at for t in self.turns if t.started_at is not None]
        return min(stamps, default=None)

    @property
    def last_at(self) -> datetime | None:
        stamps = [t.last_at for t in self.turns if t.last_at is not None]
        return max(stamps, default=None)

    @property
    def typed_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.typed]


def read_session(path: Path) -> Session:
    """Parse one transcript file. Never raises for record-level problems.

    Problems are counted in ``Session.anomalies``; the file is not modified.
    """
    session = Session(session_id=path.stem, turns=[Turn(0, None, typed=False)])
    calls: dict[str, ApiCall] = {}
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                session.anomalies[Anomaly.MALFORMED_LINE] += 1
                continue
            if not isinstance(record, dict):
                session.anomalies[Anomaly.MALFORMED_LINE] += 1
                continue
            _read_record(session, calls, record)
    preamble = session.turns[0]
    if (
        len(session.turns) > 1
        and not preamble.calls
        and preamble.active_ms is None
        and not preamble.compactions
    ):
        # Nothing happened before the first typed prompt worth reporting.
        session.turns = session.turns[1:]
    return session


def _read_record(
    session: Session, calls: dict[str, ApiCall], record: dict[str, object]
) -> None:
    kind = record.get("type")
    if not isinstance(kind, str) or kind not in READ_TYPES | SKIPPED_TYPES:
        session.unknown_record_types[str(kind)] += 1
        return
    if kind in SKIPPED_TYPES:
        return
    owner = record.get("sessionId")
    if owner is not None and owner != session.session_id:
        session.anomalies[Anomaly.FOREIGN_SESSION] += 1
        return
    version = record.get("version")
    if isinstance(version, str):
        session.client_versions[version] += 1
    stamp = _timestamp(session, record.get("timestamp"))
    turn = session.turns[-1]

    if kind == "user" and record.get("promptSource") == "typed":
        turn = Turn(len(session.turns), stamp, typed=True)
        session.turns.append(turn)
    if stamp is not None:
        if turn.started_at is None:
            turn.started_at = stamp
        if turn.last_at is None or stamp > turn.last_at:
            turn.last_at = stamp

    if kind == "system":
        _read_system(session, turn, record)
    elif kind == "assistant":
        _read_assistant(session, calls, turn, record, stamp)


def _read_system(session: Session, turn: Turn, record: dict[str, object]) -> None:
    subtype = record.get("subtype")
    if subtype == "compact_boundary":
        turn.compactions += 1
    elif subtype == "turn_duration":
        duration = record.get("durationMs")
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            session.anomalies[Anomaly.BAD_DURATION] += 1
            return
        if duration < 0:
            session.anomalies[Anomaly.BAD_DURATION] += 1
            return
        turn.active_ms = (turn.active_ms or 0) + int(duration)


def _read_assistant(
    session: Session,
    calls: dict[str, ApiCall],
    turn: Turn,
    record: dict[str, object],
    stamp: datetime | None,
) -> None:
    message = record.get("message")
    if not isinstance(message, dict):
        session.anomalies[Anomaly.ASSISTANT_WITHOUT_ID] += 1
        return
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        session.anomalies[Anomaly.ASSISTANT_WITHOUT_ID] += 1
        return
    raw_usage = message.get("usage")
    usage, signature = _usage(session, raw_usage)

    call = calls.get(message_id)
    if call is None:
        call = ApiCall(
            message_id=message_id,
            timestamp=stamp,
            model=_text(message.get("model")),
            service_tier=_text(_get(raw_usage, "service_tier")),
            speed=_text(_get(raw_usage, "speed")),
            inference_geo=_text(_get(raw_usage, "inference_geo")),
            stop_reason=_text(message.get("stop_reason")),
            usage=usage,
            sidechain=record.get("isSidechain") is True,
            signature=signature,
        )
        calls[message_id] = call
        turn.calls.append(call)
    elif not call.conflicted and signature != call.signature:
        # Two lines of one message disagree: neither is trusted.
        session.anomalies[Anomaly.CONFLICTING_DUPLICATE] += 1
        call.conflicted = True
        call.usage = None
    if call.stop_reason is None:
        call.stop_reason = _text(message.get("stop_reason"))
    _read_tool_uses(call, message.get("content"))


def _usage(
    session: Session, raw: object
) -> tuple[Usage | None, tuple[object, ...] | None]:
    if not isinstance(raw, dict):
        session.anomalies[Anomaly.INCOMPLETE_USAGE] += 1
        return None, None
    for key in raw:
        if key not in KNOWN_USAGE_KEYS:
            session.unknown_usage_keys[str(key)] += 1
    values = [raw.get(name) for name in REQUIRED_USAGE]
    if not all(_count(value) for value in values):
        session.anomalies[Anomaly.INCOMPLETE_USAGE] += 1
        return None, None
    input_tokens, output, cache_write, cache_read = (int(str(v)) for v in values)

    iterations = raw.get("iterations")
    if isinstance(iterations, list) and len(iterations) > 1:
        # Observed only with one entry, equal to the top level. More than one
        # has unverified semantics: report it, still count the top level once.
        session.anomalies[Anomaly.MULTIPLE_ITERATIONS] += 1

    split = raw.get("cache_creation")
    five = _get(split, "ephemeral_5m_input_tokens")
    hour = _get(split, "ephemeral_1h_input_tokens")
    five_m = int(str(five)) if _count(five) else 0
    one_h = int(str(hour)) if _count(hour) else 0
    if five_m + one_h > cache_write:
        session.anomalies[Anomaly.CACHE_SPLIT_EXCEEDS_TOTAL] += 1
        return None, None
    thinking_raw = _get(raw.get("output_tokens_details"), "thinking_tokens")
    thinking = int(str(thinking_raw)) if _count(thinking_raw) else None
    if thinking is not None and thinking > output:
        session.anomalies[Anomaly.INCOMPLETE_USAGE] += 1
        return None, None

    usage = Usage(
        input=input_tokens,
        output=output,
        cache_write_5m=five_m,
        cache_write_1h=one_h,
        cache_write_unspecified=cache_write - five_m - one_h,
        cache_read=cache_read,
        thinking=thinking,
    )
    signature = (
        input_tokens,
        output,
        cache_write,
        cache_read,
        five_m,
        one_h,
        thinking,
    )
    return usage, signature


def _read_tool_uses(call: ApiCall, content: object) -> None:
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        block_id = block.get("id")
        name = block.get("name")
        if not isinstance(block_id, str) or block_id in call.tool_uses:
            continue
        call.tool_uses[block_id] = (
            name if isinstance(name, str) and name in TOOL_ALLOWLIST else OTHER_TOOL
        )


def _timestamp(session: Session, value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        session.anomalies[Anomaly.BAD_TIMESTAMP] += 1
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        session.anomalies[Anomaly.BAD_TIMESTAMP] += 1
        return None
    if parsed.utcoffset() is None:
        session.anomalies[Anomaly.BAD_TIMESTAMP] += 1
        return None
    return parsed


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _get(container: object, key: str) -> object:
    return container.get(key) if isinstance(container, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
