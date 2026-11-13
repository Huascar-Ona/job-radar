"""The local, Git-ignored map from sessions to milestones (D3, D8).

Real Claude Code session ids and absolute transcript paths live only here,
in ``.harness/metrics.local.toml`` (ignored by Git). Committed artifacts use
milestone ids and session *aliases* only::

    schema_version = 1
    transcripts_dir = "/absolute/path/to/.claude/projects/<project>"

    [[sessions]]
    alias = "s1"
    id = "<real session uuid>"

    [[segments]]
    milestone = "4F"
    session = "s1"
    from_turn = 8
    to_turn = 13
    boundary = "inferred"     # or "reviewed", once a person has checked it
    basis = "commit_time"     # or "manual"

A segment is a contiguous range of typed-prompt turns of one session. A
milestone may have several segments, in several sessions; segments of one
session must not overlap. Nothing here is decided automatically:
``suggest-segments`` only prints suggestions.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
DEFAULT_PATH: Final = Path(".harness/metrics.local.toml")
BOUNDARY_STATES: Final = ("inferred", "reviewed")
BASES: Final = ("commit_time", "manual")
_MILESTONE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,15}")
_SESSION_ID = re.compile(r"[0-9a-fA-F-]{8,64}")


class ConfigError(ValueError):
    """The local configuration is invalid. Messages never quote real ids."""


@dataclass(frozen=True, slots=True)
class SessionRef:
    alias: str
    session_id: str


@dataclass(frozen=True, slots=True)
class Segment:
    milestone: str
    session: str
    from_turn: int
    to_turn: int
    boundary: str
    basis: str

    def covers(self, turn: int) -> bool:
        return self.from_turn <= turn <= self.to_turn


@dataclass(frozen=True, slots=True)
class LocalConfig:
    transcripts_dir: Path
    sessions: tuple[SessionRef, ...]
    segments: tuple[Segment, ...]

    def session(self, alias: str) -> SessionRef:
        for ref in self.sessions:
            if ref.alias == alias:
                return ref
        msg = f"unknown session alias {alias!r}"
        raise ConfigError(msg)

    def alias_of(self, session_id: str) -> str | None:
        for ref in self.sessions:
            if ref.session_id == session_id:
                return ref.alias
        return None

    def transcript(self, alias: str) -> Path:
        return self.transcripts_dir / f"{self.session(alias).session_id}.jsonl"

    def milestones(self) -> list[str]:
        seen: list[str] = []
        for segment in self.segments:
            if segment.milestone not in seen:
                seen.append(segment.milestone)
        return seen


def default_transcripts_dir(repository: Path) -> Path:
    """Where Claude Code keeps this repository's transcripts by default."""
    slug = str(repository.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def load_config(path: Path, *, repository: Path) -> LocalConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        msg = f"no local configuration at {path}; see docs/metrics/README.md"
        raise ConfigError(msg) from None
    except tomllib.TOMLDecodeError:
        msg = "local configuration is not valid TOML"
        raise ConfigError(msg) from None
    return parse_config(data, repository=repository)


def parse_config(data: dict[str, object], *, repository: Path) -> LocalConfig:
    if data.get("schema_version") != SCHEMA_VERSION:
        msg = f"schema_version must be {SCHEMA_VERSION}"
        raise ConfigError(msg)
    directory = data.get("transcripts_dir")
    if directory is None:
        transcripts = default_transcripts_dir(repository)
    elif isinstance(directory, str) and directory:
        transcripts = Path(directory).expanduser()
    else:
        msg = "transcripts_dir must be a path"
        raise ConfigError(msg)

    sessions = tuple(
        _session(i, raw) for i, raw in enumerate(_tables(data, "sessions"))
    )
    aliases = [s.alias for s in sessions]
    if len(set(aliases)) != len(aliases):
        msg = "session aliases must be unique"
        raise ConfigError(msg)
    if len({s.session_id for s in sessions}) != len(sessions):
        msg = "a session is listed twice"
        raise ConfigError(msg)

    segments = tuple(
        _segment(i, raw) for i, raw in enumerate(_tables(data, "segments"))
    )
    for segment in segments:
        if segment.session not in aliases:
            msg = (
                f"segment of {segment.milestone} names unknown alias"
                f" {segment.session!r}"
            )
            raise ConfigError(msg)
    for i, a in enumerate(segments):
        for b in segments[i + 1 :]:
            if (
                a.session == b.session
                and a.from_turn <= b.to_turn
                and b.from_turn <= a.to_turn
            ):
                msg = (
                    f"segments of {a.milestone} and {b.milestone} overlap in"
                    f" session {a.session!r}"
                )
                raise ConfigError(msg)
    return LocalConfig(transcripts, sessions, segments)


def _tables(data: dict[str, object], key: str) -> list[object]:
    value = data.get(key, [])
    if not isinstance(value, list):
        msg = f"{key} must be an array of tables"
        raise ConfigError(msg)
    return value


def _session(index: int, raw: object) -> SessionRef:
    if not isinstance(raw, dict) or set(raw) != {"alias", "id"}:
        msg = f"sessions[{index}] must have exactly: alias, id"
        raise ConfigError(msg)
    alias, session_id = raw["alias"], raw["id"]
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
        msg = f"sessions[{index}].alias must match {_ALIAS.pattern}"
        raise ConfigError(msg)
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        msg = f"sessions[{index}].id is not a session id"
        raise ConfigError(msg)
    return SessionRef(alias, session_id)


def _segment(index: int, raw: object) -> Segment:
    fields = {"milestone", "session", "from_turn", "to_turn", "boundary", "basis"}
    if not isinstance(raw, dict) or set(raw) != fields:
        msg = f"segments[{index}] must have exactly: {', '.join(sorted(fields))}"
        raise ConfigError(msg)
    milestone, session = raw["milestone"], raw["session"]
    first, last = raw["from_turn"], raw["to_turn"]
    if not isinstance(milestone, str) or not _MILESTONE.fullmatch(milestone):
        msg = f"segments[{index}].milestone must match {_MILESTONE.pattern}"
        raise ConfigError(msg)
    if not isinstance(session, str):
        msg = f"segments[{index}].session must be an alias"
        raise ConfigError(msg)
    first = _turn(first, f"segments[{index}].from_turn")
    last = _turn(last, f"segments[{index}].to_turn")
    if first > last:
        msg = f"segments[{index}] ends before it starts"
        raise ConfigError(msg)
    boundary, basis = raw["boundary"], raw["basis"]
    if boundary not in BOUNDARY_STATES:
        msg = f"segments[{index}].boundary must be one of {', '.join(BOUNDARY_STATES)}"
        raise ConfigError(msg)
    if basis not in BASES:
        msg = f"segments[{index}].basis must be one of {', '.join(BASES)}"
        raise ConfigError(msg)
    return Segment(milestone, session, first, last, str(boundary), str(basis))


def _turn(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f"{what} must be a non-negative integer"
        raise ConfigError(msg)
    return value
