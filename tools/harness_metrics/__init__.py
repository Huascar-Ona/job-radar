"""H1: local, read-only observability for Claude Code development sessions.

Reads Claude Code session transcripts (``~/.claude/projects/<project>/*.jsonl``)
for metadata only, aggregates token usage, time and activity per session,
turn and milestone, estimates cost from a versioned pricing table, and joins
a hand-maintained ledger of verification outcomes and human interventions.

Privacy is a requirement, not a feature: see ``transcript`` for the
allowlist, and docs/metrics/README.md for what may be committed.
"""
