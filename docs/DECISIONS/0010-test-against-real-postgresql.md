# ADR-0010: Test against real PostgreSQL

- Status: Accepted
- Date: 2026-09-15

## Context

The design depends on PostgreSQL-specific behavior: `INSERT ... ON CONFLICT`,
`jsonb`, `timestamptz`, and a UNIQUE constraint that must genuinely reject
duplicates. SQLite would make the test suite faster and simpler to run.

## Decision

Any test that asserts database behavior runs against real PostgreSQL, provided
via Docker Compose. SQLite is not an acceptable substitute.

The suite is layered:

- Unit: normalizers, pure and fixture-driven, no I/O.
- Unit: collectors against a stubbed httpx transport, never live network.
- Integration: repositories against real PostgreSQL.
- Acceptance: the CLI end to end, stubbed HTTP and a real database.

At least one test must violate the unique constraint directly, bypassing the
upsert path, to prove the constraint lives in the migration and not only in
application code.

## Consequences

- Running tests requires Docker. Accepted.
- The central idempotency guarantee is verified against the engine that
  actually enforces it. An SQLite-green suite would say nothing about
  production behavior.
