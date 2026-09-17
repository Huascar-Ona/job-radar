# ADR-0004: Defer job deactivation

- Status: Accepted
- Date: 2026-09-15

## Context

`is_active` is a required field, but no MVP requirement defines when a job
stops being active. The obvious implementation — "not present in the latest
crawl" — interacts badly with mandated per-company failure isolation: a board
that times out would look identical to a board that closed every posting, and
would silently deactivate all of that company's jobs.

## Decision

For the MVP, all successfully ingested jobs are active. Inserted and
re-observed jobs are written with `is_active = true`. Nothing sets it false.

Deactivation is deferred. When introduced, it may only reason about a job's
absence from a crawl of that company that **succeeded**.

A failed company crawl must never modify the `is_active` or `last_seen_at` of
that company's existing jobs.

To keep that future rule implementable, per-company crawl outcomes are
persisted now (ADR-0008), even though nothing consumes them yet.

## Consequences

- `is_active` is constant in the MVP. This is deliberate, and the column exists
  so that adding deactivation later needs no backfill of invented history.
- The crawl outcome tables are built ahead of their consumer. This is accepted
  cost: the failure mode they prevent produces plausible-looking wrong data,
  which is far more expensive to detect and repair than to prevent.
