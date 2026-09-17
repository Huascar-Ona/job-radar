# ADR-0002: Job identity and idempotent upserts

- Status: Accepted
- Date: 2026-09-15

## Context

Re-running a crawl must not duplicate jobs. Identity must be stable across
runs and meaningful across future sources.

## Decision

A job is identified by `(source, source_job_id)`, enforced by a database
UNIQUE constraint.

`source_job_id` is stored as **text**, even though Greenhouse ids are numeric,
because Lever and Ashby use UUIDs and a type change later would require a
migration of the identity column.

Persistence uses `INSERT ... ON CONFLICT (source, source_job_id) DO UPDATE`.
The application never reads-then-writes to decide insert vs. update.

A relisted posting that the source assigns a new `source_job_id` is a new Job
Radar job for the MVP. Logical deduplication across relistings is out of scope.

## Consequences

- Idempotency is a database property, not an application convention. It holds
  even if two crawls execute concurrently.
- `first_seen_at` must be excluded from the `DO UPDATE SET` list, which is how
  its preservation is guaranteed.
- Insert-vs-update counters must be derived from the statement result
  (e.g. `xmax = 0` in a `RETURNING` clause) rather than from a prior read.
- A company that relists a job produces two rows. Accepted for the MVP; revisit
  when duplicate noise becomes visible downstream.
