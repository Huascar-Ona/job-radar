# ADR-0013: Source consistency via a composite foreign key

- Status: Accepted
- Date: 2026-09-15

## Context

`jobs` carries both `company_id` and `source`. `companies` carries `source`
too. Nothing required the two to agree, so a Lever job could reference a
Greenhouse company. Nothing in the application would have noticed: both rows
are individually valid, and the inconsistency only surfaces later as wrong
results in any per-source query.

The alternative was to drop `jobs.source` and always join to `companies`. That
loses the ability to enforce `UNIQUE (source, source_job_id)`, which is the
identity invariant the whole idempotency design rests on.

## Decision

Keep `jobs.source`, and constrain it:

- `companies` gains `UNIQUE (id, source)`, the key the composite reference
  needs. It is not an identity constraint; `id` alone is already unique.
- `jobs` replaces its single-column foreign key with
  `(company_id, source) -> companies (id, source)`.
- RESTRICT on delete is preserved and extended to update.

`UNIQUE (source, source_job_id)` is unchanged.

Introduced by migration `0002_source_consistency`, separately from the initial
schema, so the change is legible in history.

## Consequences

- A job whose source disagrees with its company is rejected by PostgreSQL, on
  INSERT and on UPDATE alike.
- A company's `source` is effectively immutable once it has jobs. Correct: a
  board token identifies a board *on a given provider*, so changing the
  provider is a different company, not a rename.
- Tests that attached a Lever job to a Greenhouse company had to be corrected.
  That they existed is precisely the gap this closes.
- Adding Lever or Ashby needs no schema change.
