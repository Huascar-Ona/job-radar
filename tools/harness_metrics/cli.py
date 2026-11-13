"""``python -m tools.harness_metrics``: read-only session metrics (H1).

Every command only reads: transcripts, the local configuration, the pricing
table, the outcomes ledger and, for ``suggest-segments``, ``git log``. The
only write is ``milestones --write-snapshots DIR``, which writes sanitized
JSON. Real session ids are shown only with ``--show-ids``; snapshots never
contain them.

Exit codes: 0 done; 1 invalid configuration, pricing or ledger, or, with
``--strict``, any parsing anomaly; 2 usage error.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from tools.harness_metrics.aggregate import Totals, total
from tools.harness_metrics.boundaries import read_commits, suggest
from tools.harness_metrics.config import (
    DEFAULT_PATH,
    ConfigError,
    LocalConfig,
    default_transcripts_dir,
    load_config,
)
from tools.harness_metrics.outcomes import OutcomeError, load_outcomes
from tools.harness_metrics.pricing import PricingError, PricingTable, load_pricing
from tools.harness_metrics.report import milestone_reports, render_table, snapshot
from tools.harness_metrics.transcript import Session, read_session

DEFAULT_PRICING = Path("docs/metrics/pricing.toml")
DEFAULT_OUTCOMES = Path("docs/metrics/outcomes.jsonl")
EXIT_OK, EXIT_INVALID = 0, 1


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    out = stdout or sys.stdout
    args = _parser().parse_args(argv)
    repository = Path(args.repository).resolve()
    try:
        return int(args.handler(args, repository, out))
    except (ConfigError, PricingError, OutcomeError) as exc:
        sys.stderr.write(f"harness-metrics: {exc}\n")
        return EXIT_INVALID


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-metrics",
        description="Read-only metrics for Claude Code sessions (H1).",
    )
    parser.add_argument("--repository", default=".", help="repository root")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_PATH),
        help=f"local, Git-ignored configuration (default: {DEFAULT_PATH})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sessions = commands.add_parser("sessions", help="list transcripts")
    sessions.add_argument("--show-ids", action="store_true")
    sessions.add_argument("--json", action="store_true")
    sessions.add_argument("--strict", action="store_true")
    sessions.set_defaults(handler=_sessions)

    one = commands.add_parser("session", help="per-turn breakdown of one session")
    one.add_argument("alias")
    one.add_argument("--json", action="store_true")
    one.add_argument("--strict", action="store_true")
    one.set_defaults(handler=_session)

    hint = commands.add_parser(
        "suggest-segments", help="suggest milestone boundaries from commit times"
    )
    hint.add_argument("alias")
    hint.set_defaults(handler=_suggest)

    report = commands.add_parser("milestones", help="compare milestones")
    report.add_argument("--pricing", default=str(DEFAULT_PRICING))
    report.add_argument("--outcomes", default=str(DEFAULT_OUTCOMES))
    report.add_argument("--json", action="store_true")
    report.add_argument("--strict", action="store_true")
    report.add_argument(
        "--write-snapshots", metavar="DIR", help="write sanitized JSON per milestone"
    )
    report.add_argument(
        "--only",
        action="append",
        metavar="MILESTONE",
        help="report only this milestone (repeatable)",
    )
    report.set_defaults(handler=_milestones)

    check = commands.add_parser("validate", help="validate configuration files")
    check.add_argument("--pricing", default=str(DEFAULT_PRICING))
    check.add_argument("--outcomes", default=str(DEFAULT_OUTCOMES))
    check.set_defaults(handler=_validate)
    return parser


# --- commands ----------------------------------------------------------------------


def _sessions(args: argparse.Namespace, repository: Path, out: TextIO) -> int:
    config = _optional_config(args, repository)
    directory = (
        config.transcripts_dir if config else default_transcripts_dir(repository)
    )
    parsed = sorted(
        (read_session(path) for path in sorted(directory.glob("*.jsonl"))),
        key=lambda s: (s.first_at is None, s.first_at),
    )
    rows = []
    unmapped = 0
    for session in parsed:
        alias = config.alias_of(session.session_id) if config else None
        if alias is None:
            unmapped += 1
            alias = f"unmapped-{unmapped}"
        totals = total(session.calls)
        row: dict[str, object] = {
            "session": alias,
            "first": _iso(session.first_at),
            "last": _iso(session.last_at),
            "typed_turns": len(session.typed_turns),
            **_token_fields(totals),
            "active_agent_ms": sum(t.active_ms or 0 for t in session.turns),
            "anomalies": sum(session.anomalies.values()),
            "unknown_record_types": sum(session.unknown_record_types.values()),
        }
        if args.show_ids:
            row["session_id"] = session.session_id
        rows.append(row)
    if args.json:
        out.write(json.dumps(rows, indent=2) + "\n")
    else:
        out.write(_table(rows))
    return _strict_exit(args, parsed)


def _session(args: argparse.Namespace, repository: Path, out: TextIO) -> int:
    config = load_config(Path(args.config), repository=repository)
    session = read_session(config.transcript(args.alias))
    rows = []
    for turn in session.turns:
        totals = total(turn.calls)
        rows.append(
            {
                "turn": turn.index,
                "started": _iso(turn.started_at),
                **_token_fields(totals),
                "active_s": None if turn.active_ms is None else turn.active_ms // 1000,
                "compactions": turn.compactions,
                "tools": dict(sorted(totals.tools.items())),
            }
        )
    if args.json:
        out.write(json.dumps(rows, indent=2) + "\n")
    else:
        out.write(_table([{k: v for k, v in r.items() if k != "tools"} for r in rows]))
    return _strict_exit(args, [session])


def _suggest(args: argparse.Namespace, repository: Path, out: TextIO) -> int:
    config = load_config(Path(args.config), repository=repository)
    session = read_session(config.transcript(args.alias))
    suggestions = suggest(session, read_commits(repository))
    if not suggestions:
        out.write("No commit falls within this session.\n")
    for item in suggestions:
        out.write(
            f"turn {item.turn}: commit {item.commit.short_sha}"
            f" at {item.commit.committed_at.isoformat()} ({item.commit.subject})\n"
        )
    out.write(
        "Suggestions only (basis: commit_time). Review them, then record segments"
        ' in the local configuration with boundary = "inferred" or "reviewed".\n'
    )
    return EXIT_OK


def _milestones(args: argparse.Namespace, repository: Path, out: TextIO) -> int:
    config = load_config(Path(args.config), repository=repository)
    pricing = _pricing(repository / args.pricing)
    outcomes = load_outcomes(repository / args.outcomes)
    sessions: dict[str, Session | None] = {}
    for ref in config.sessions:
        path = config.transcript(ref.alias)
        sessions[ref.alias] = read_session(path) if path.exists() else None
    reports = milestone_reports(config, sessions, pricing=pricing, outcomes=outcomes)
    if args.only:
        unknown = sorted(set(args.only) - {r.milestone for r in reports})
        if unknown:
            msg = f"milestones not in the local configuration: {', '.join(unknown)}"
            raise ConfigError(msg)
        reports = [r for r in reports if r.milestone in args.only]
    snapshots = [snapshot(r) for r in reports]
    if args.write_snapshots:
        directory = Path(args.write_snapshots)
        directory.mkdir(parents=True, exist_ok=True)
        for data in snapshots:
            path = directory / f"{data['milestone']}.json"
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if args.json:
        out.write(json.dumps(snapshots, indent=2) + "\n")
    else:
        out.write(render_table(reports))
    return _strict_exit(args, [s for s in sessions.values() if s is not None])


def _validate(args: argparse.Namespace, repository: Path, out: TextIO) -> int:
    config_path = Path(args.config)
    if config_path.exists():
        config = load_config(config_path, repository=repository)
        out.write(
            f"local configuration: ok ({len(config.sessions)} sessions,"
            f" {len(config.segments)} segments)\n"
        )
    else:
        out.write("local configuration: absent (optional)\n")
    table = load_pricing(repository / args.pricing)
    out.write(f"pricing: ok (version {table.version}, {len(table.rates)} rates)\n")
    outcomes = load_outcomes(repository / args.outcomes)
    out.write(f"outcomes: ok ({len(outcomes)} entries)\n")
    return EXIT_OK


# --- helpers -------------------------------------------------------------------------


def _optional_config(args: argparse.Namespace, repository: Path) -> LocalConfig | None:
    path = Path(args.config)
    return load_config(path, repository=repository) if path.exists() else None


def _pricing(path: Path) -> PricingTable | None:
    return load_pricing(path) if path.exists() else None


def _strict_exit(args: argparse.Namespace, sessions: Sequence[Session]) -> int:
    if getattr(args, "strict", False) and any(
        s.anomalies or s.unknown_record_types or s.unknown_usage_keys for s in sessions
    ):
        sys.stderr.write("harness-metrics: anomalies or schema drift found\n")
        return EXIT_INVALID
    return EXIT_OK


def _token_fields(totals: Totals) -> dict[str, object]:
    return {
        "calls": totals.calls,
        "unknown_usage": totals.calls_unknown_usage,
        "input": totals.input,
        "output": totals.output,
        "thinking": totals.thinking,
        "cache_w_5m": totals.cache_write_5m,
        "cache_w_1h": totals.cache_write_1h,
        "cache_read": totals.cache_read,
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "(nothing to show)\n"
    columns = list(rows[0])
    cells = [
        ["-" if r.get(c) is None else str(r.get(c)) for c in columns] for r in rows
    ]
    widths = [
        max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)
    ]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))]
    lines += [
        "  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)).rstrip()
        for row in cells
    ]
    return "\n".join(lines) + "\n"
