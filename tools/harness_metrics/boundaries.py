"""Suggest milestone boundaries from Git commit times. Suggestions only.

A commit made during a session usually closes a milestone, so the turn in
which it falls is a *candidate* last turn. Nothing is written: a person
reviews the suggestion and records the segment in the local configuration
(boundary ``inferred`` until reviewed, then ``reviewed``).
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.harness_metrics.transcript import Session


@dataclass(frozen=True, slots=True)
class Commit:
    short_sha: str
    committed_at: datetime
    subject: str


@dataclass(frozen=True, slots=True)
class Suggestion:
    commit: Commit
    #: The turn during which the commit was made: a candidate last turn.
    turn: int


def read_commits(repository: Path, limit: int = 200) -> list[Commit]:
    """Recent commits, read-only (``git log``)."""
    completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "log", f"-{limit}", "--format=%h%x1f%cI%x1f%s"],  # noqa: S607
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for line in completed.stdout.splitlines():
        sha, stamp, subject = line.split("\x1f", 2)
        commits.append(Commit(sha, datetime.fromisoformat(stamp), subject))
    return commits


def suggest(session: Session, commits: list[Commit]) -> list[Suggestion]:
    """Each commit made while a typed turn of ``session`` was the latest one."""
    starts = [
        (turn.started_at, turn.index)
        for turn in session.typed_turns
        if turn.started_at is not None
    ]
    if not starts:
        return []
    first = min(start for start, _ in starts)
    end = session.last_at
    suggestions = []
    for commit in sorted(commits, key=lambda c: c.committed_at):
        if commit.committed_at < first:
            continue
        if end is not None and commit.committed_at > end:
            continue
        turn = max(index for start, index in starts if start <= commit.committed_at)
        suggestions.append(Suggestion(commit, turn))
    return suggestions
