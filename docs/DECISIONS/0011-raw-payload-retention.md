# ADR-0011: Raw payload retention

- Status: Accepted
- Date: 2026-09-15

## Context

The spec requires preserving the raw source payload for debugging, and
`raw_data` is a required normalized field. `content=true` is mandatory for
Greenhouse (ADR-0006), so each payload embeds full HTML job content.

Measured on a live board: ~10 KB per job on average, ~13 KB at the tail, and a
board payload of 1.0 MB for 83 jobs. Writing that on every job on every crawl,
regardless of change, is write amplification plus dead-tuple churn in
PostgreSQL.

## Decision

`raw_data` is a `jsonb` column on `jobs` holding the **latest** payload only.
Historical snapshots are not retained.

`jobs.content_hash` stores a hash over the source-derived fields. On conflict:

- hash changed: all source-derived fields, `raw_data` and `last_seen_at` are
  written; the job counts as `existing` and `updated`.
- hash unchanged: only `last_seen_at` is written; the job counts as `existing`
  but not `updated`.

Historical payload retention is explicitly deferred. A `job_snapshots` table is
not introduced.

## Consequences

- A stable board costs one narrow timestamp write per job per crawl instead of
  a 10 KB rewrite.
- `updated` becomes a meaningful counter — it reflects real source change
  rather than crawl frequency.
- "What did this posting look like last week" is unanswerable. Accepted, and
  not retroactively recoverable: introducing snapshots later captures history
  only from that point on.
- The hash must cover exactly the normalized source-derived fields. If it is
  computed over the raw payload instead, volatile fields unrelated to the job
  would defeat the guard.
