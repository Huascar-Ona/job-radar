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

## Erratum, 2026-11-11

Two statements above do not match the implemented upsert
(`src/job_radar/persistence/job_repository.py`). The decision itself, keeping
only the latest payload and not rewriting it when nothing changed, holds.

1. **"Hash unchanged: only `last_seen_at` is written"** and **"one narrow
   timestamp write per job per crawl"**. The implemented conflict branch also
   writes, for an unchanged job, `is_active`, `updated_at` and
   `previous_content_hash`, and rewrites the other source-derived fields and
   `content_hash` with identical values. What is avoided is replacing the
   stored ~10 KB `raw_data` value: it is kept as it is. The row is still
   updated, so each crawl still produces a new row version per job. SPEC,
   "Update semantics on re-observation", lists the columns.
2. **"The hash must cover exactly the normalized source-derived fields. If it
   is computed over the raw payload instead, volatile fields unrelated to the
   job would defeat the guard."** The implemented hash (`HASHED_FIELDS`)
   covers the normalized source-derived fields **and `raw_data`**. A change to
   any payload field, including one Job Radar does not normalize, therefore
   counts as `updated` and replaces `raw_data`. Whether the hash should
   exclude `raw_data` is an open question; answering it would change the
   decision, so it needs its own ADR rather than an erratum.
