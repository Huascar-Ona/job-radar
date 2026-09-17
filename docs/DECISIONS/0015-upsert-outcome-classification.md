# ADR-0015: Classify upsert outcomes from the row, not from a snapshot read

- Status: Accepted
- Date: 2026-09-16

## Context

The job upsert must report whether an observation inserted a row, changed it,
changed nothing, or hit an identity that belongs to a different company. The
first implementation obtained that classification from a CTE alongside the
upsert:

```sql
WITH prior AS (SELECT id, company_id, content_hash FROM jobs WHERE ...),
     upserted AS (INSERT ... ON CONFLICT ... DO UPDATE ...
                  WHERE jobs.company_id = EXCLUDED.company_id
                  RETURNING id, (xmax = 0) AS inserted)
SELECT ... FROM upserted FULL OUTER JOIN prior ON true
```

Uniqueness was never in question: `UNIQUE (source, source_job_id)` was and
remains the concurrency primitive. The classification was the problem.

Every CTE in one statement shares that statement's snapshot. `ON CONFLICT`
does not: it must see rows committed after the snapshot, otherwise it could
not enforce uniqueness, and for `DO UPDATE` it resolves against the live row
under a row lock. So `prior` can see nothing while the conflict branch updates
a row a concurrent writer just committed.

Reproduced deterministically against PostgreSQL 17 by holding writer A's
transaction open until writer B was observably blocked in `pg_stat_activity`:

- **Same content, both writers.** B returned `inserted = false` with
  `prior_content_hash = NULL`, so `NULL != incoming` classified it as
  `UPDATED`. B changed nothing; the correct answer was `UNCHANGED`.
- **Different companies.** `prior` was empty *and* the `WHERE` skipped the
  conflict branch, so the FULL OUTER JOIN produced **zero rows** and `.one()`
  raised `NoResultFound`. The anomaly the design exists to surface became an
  exception instead.

The stored row was correct in both cases. What was wrong was what the
repository reported about it.

## Decision

Classify from the returned row, and obtain the pre-update state inside the
conflict branch where `jobs.*` is the live row.

- `jobs.previous_content_hash` is added, set from `jobs.content_hash` inside
  `DO UPDATE` before `content_hash` is overwritten.
- Every `SET` is guarded by `jobs.company_id = EXCLUDED.company_id`, and the
  `WHERE` is removed, so the statement always returns a row. A foreign
  identity leaves every column at its existing value and still reports.
- `RETURNING id, company_id, previous_content_hash` drives the classification:

| Signal | Outcome |
|---|---|
| `company_id` differs from the incoming one | `COMPANY_CONFLICT` |
| `previous_content_hash IS NULL` | `INSERTED` |
| `previous_content_hash` equals the incoming hash | `UNCHANGED` |
| otherwise | `UPDATED` |

The CTE and the `FULL OUTER JOIN` are gone.

## On `(xmax = 0) AS inserted`

Classification: **implementation-dependent but conventional.** It works, is
widely used, and has been stable for many major versions, but no PostgreSQL
documentation defines `xmax` on a row returned from `ON CONFLICT DO UPDATE`.
It is an observable consequence of how the conflict path is implemented.

It has been removed, not because it misbehaved — it classified correctly in
every test — but because `previous_content_hash IS NULL` answers the same
question from our own data, with semantics we define. Losing a dependency on
engine internals was a side effect of fixing the real defect, not the reason
for the change.

Alternatives considered and rejected:

- `created_at = updated_at` — both default to `now()`, which is the
  *transaction* timestamp, so an insert and a later update in the same
  transaction are indistinguishable.
- `first_seen_at = observed_at` — true for an insert, but also true for a
  re-observation that happens to share the instant.
- `RETURNING OLD.content_hash` — exact, but PostgreSQL 18 only. The project
  targets 14-compatible SQL.

## Consequences

- Classification no longer depends on statement snapshots, so it is correct
  under concurrency. Proven by eight race tests that fail against the previous
  implementation.
- The company-conflict path costs one no-op row update: the tuple is rewritten
  with identical column values rather than skipped. Anomalies are rare, and a
  reportable result is worth more than a saved write. Column values are
  unchanged, which is what "preserve the existing row" means here.
- One extra nullable text column per job.
- The statement is simpler: no CTE, no outer join, no system columns.
- Transaction ownership is unchanged; repositories still never commit.
