# ADR-0008: Explicit crawl outcome model

- Status: Accepted
- Date: 2026-09-15

## Context

Failure isolation means a run can partially succeed. "Some companies worked"
is not expressible as a boolean, and logs are not queryable state.

Deferred deactivation (ADR-0004) additionally requires knowing which companies
were crawled *successfully*, which cannot be reconstructed after the fact.

## Decision

Crawl outcomes are persisted, not merely logged.

A run has one status:

| Status | Condition |
|---|---|
| success | every attempted company succeeded |
| partial_success | at least one succeeded and at least one failed |
| failed | at least one company attempted, none succeeded |

A run over zero enabled companies is `success`.

Each company within a run records its own status, counters, and error details.

Status values are stored as text with a CHECK constraint rather than a native
PostgreSQL enum, because adding a value to a native enum is an awkward
migration and more statuses are likely.

Process exit code is explicitly **not** decided here. The MVP exits 0 for all
three statuses; only an unhandled crash exits non-zero. Whether partial failure
should be non-zero is deferred until a scheduler actually consumes the code.

## Consequences

- "Which boards have been failing this week" is a SQL query.
- Future deactivation has the successful-crawl record it requires.
- Outcome rows accumulate per run per company and will need a retention policy
  eventually. Not an MVP concern.
