# Job Radar MVP Specification

## Objective

Discover newly available software engineering jobs from configured
Greenhouse job boards and persist them reliably.

Ingestion is deliberately unfiltered. The MVP persists every job published
on a configured board. Role, seniority and geography selection are
downstream concerns and must not live in collectors (ADR-0005).

## Definitions

- **Source**: an external job board provider. The MVP supports `greenhouse`.
- **Board token**: the provider-scoped identifier of a company's job board.
  It is the stable company identifier (ADR-0007).
- **Company**: a configured board, identified by `(source, board_token)`.
  Its display name is mutable metadata and carries no identity.
- **Observation**: one successful fetch + normalization of a job during a crawl.
- **Crawl run**: one execution of the ingestion pipeline over all enabled companies.

## Configuration

Enabled boards are declared in `config/boards.yml`, under version control. Each
entry carries `source`, `board_token`, `display_name` and `enabled` (ADR-0012).

This file is the sole source of configured boards for the MVP. Database-managed
configuration and an admin UI are explicitly out of scope.

## Observation versus stored job

These are two different things and must not be conflated.

A **normalized observation** is what a normalizer produces from one source
payload. It records what the source said and *when Job Radar looked*. A
normalizer cannot know whether Job Radar has seen that job before, so the
observation carries neither `first_seen_at` nor `last_seen_at`. It carries
`observed_at`.

A **stored job** is the row in the `jobs` table. It carries the two
observation-history columns, both derived from `observed_at` at write time.

`observed_at` is **not** a column on the `jobs` table. It is an observation
value that persistence consumes; storing it would duplicate `last_seen_at`.

### Normalized observation fields

| Field | Nullable | Origin |
|---|---|---|
| source | no | system |
| source_job_id | no | source |
| company | no | configuration |
| title | no | source |
| location | yes | source |
| department | yes | source |
| description | yes | source |
| job_url | no | source |
| apply_url | yes | source |
| source_published_at | yes | source |
| source_updated_at | yes | source |
| observed_at | no | Job Radar (injected clock) |
| is_active | no | Job Radar |
| raw_data | no | source |

### Stored job columns derived from the observation

| Column | On INSERT | On CONFLICT UPDATE |
|---|---|---|
| first_seen_at | `observed_at` | **preserved, never assigned** |
| last_seen_at | `observed_at` | `observed_at` |

Every other field is carried straight across from the observation.

Fields unavailable from a source must remain null rather than being inferred.

`department` was added to the model so that downstream role filtering has a
first-class column to reason about instead of parsing `raw_data`.

## Greenhouse MVP

The system must:

1. Fetch jobs from configured Greenhouse boards using the public Board API
   with `content=true` (ADR-0006).
2. Normalize Greenhouse responses into the common Job model.
3. Persist jobs in PostgreSQL.
4. Prevent duplicate jobs.
5. Update `last_seen_at` when an existing job is observed.
6. Preserve `first_seen_at` when an existing job is observed again.
7. Continue crawling other configured companies if an individual board fails.
8. Produce structured logs for failures.
9. Preserve the raw source payload for debugging.
10. Record the outcome of every crawl run and of every company within it.

## Greenhouse field mapping

| Normalized field | Greenhouse Board API source |
|---|---|
| source | literal `greenhouse` |
| source_job_id | `id` (JSON number) converted to text (ADR-0002) |
| company | configured display name for the board token |
| title | `title` |
| location | `location.name`, null when `location` is null |
| department | `departments[0].name`, null when absent or empty |
| description | `content`, HTML-unescaped |
| job_url | `absolute_url` |
| apply_url | null — no distinct apply URL is exposed |
| source_published_at | `first_published` |
| source_updated_at | `updated_at` |
| raw_data | the unmodified job object as received |

`id` is a JSON number in the source and exceeds 32-bit range in practice
(values above 8.8e9 were observed). It is converted to text on the way in and
never stored numerically.

`first_published` supplies `source_published_at`. Should it be absent or null
for a given job, `source_published_at` is null; it is never backfilled from
`first_seen_at` or from `updated_at`.

## Observed source behavior

Verified on 2026-09-15 against three public boards (458 jobs total). See
`tests/fixtures/greenhouse/README.md`.

| Observation | Consequence for the MVP |
|---|---|
| `meta.total` equalled `len(jobs)` on every board, up to 222 jobs in one response | No pagination is implemented. A future mismatch between the two is a hard error, not a silent truncation. |
| `first_published` present and non-null on 458/458 jobs | `source_published_at` is populated for Greenhouse. Newness is source-relative, not merely radar-relative. |
| `departments`, `offices` and `content` are returned **only** when `content=true` | `content=true` is mandatory, not an optimization: three required fields depend on it. |
| `departments[]` carries a hierarchy (`parent_id`, `child_ids`); every sampled job had exactly one entry | Only `departments[0].name` is normalized. The full array stays in `raw_data`; hierarchy flattening is deferred. |
| `content` is HTML-escaped (`&lt;p&gt;`) | The normalizer unescapes once. Double-unescaping is a defect. |
| `updated_at` and `first_published` carry varying UTC offsets (`-04:00`, `-05:00`), never `Z` | Offsets must be parsed and converted to UTC, not truncated. |
| `company_name` is present in the payload | Ignored for identity and display. Configuration remains authoritative (ADR-0007). It may be used later as a drift check against `display_name`. |
| Jobs with `content=true` averaged ~10 KB and peaked at ~13 KB each; the board payload grew 79 KB to 1.0 MB | Drives the `content_hash` guard on `raw_data` (ADR-0011). |

Fields observed but deliberately not normalized in the MVP:
`internal_job_id`, `requisition_id`, `metadata`, `offices`, `language`,
`education`, `application_deadline`, `data_compliance`, and the `ai_*` fields.
All remain available in `raw_data`.

## Idempotency

A job is uniquely identified by:

(source, source_job_id)

The database must enforce:

UNIQUE(source, source_job_id)

Running the same crawl repeatedly against unchanged source data
must not create additional job records.

Upserts must rely on that unique constraint and remain correct when two
crawls execute concurrently. Read-then-write logic is not acceptable.

A relisted posting that the source assigns a new `source_job_id` is a new
Job Radar job for the MVP. Cross-listing deduplication is out of scope.

## Update semantics on re-observation

When an already-known job is observed again:

- `first_seen_at` is never modified.
- `last_seen_at` is set to the observation's `observed_at`.
- `is_active` is set to true.
- All source-derived fields are overwritten with the newly observed values.
- `raw_data` is replaced with the newest payload only when `content_hash`
  changed, so unchanged jobs do not rewrite a ~10 KB payload on every crawl
  (ADR-0011).
- A job whose `content_hash` is unchanged counts as `existing` but not
  `updated`, and only its `last_seen_at` is written.

## Timestamps

The system must distinguish between:

- source_published_at: publication time reported by the source
- source_updated_at: update time reported by the source
- observed_at: instant at which a normalizer observed the job, taken once per
  crawl from the injected clock
- first_seen_at: first time Job Radar discovered the job (stored; set from
  `observed_at` on insert only)
- last_seen_at: most recent time Job Radar observed the job (stored; set from
  `observed_at` on every observation)

If Greenhouse does not provide a publication timestamp,
source_published_at must remain null.

Job Radar must never infer source_published_at from first_seen_at.

All timestamps are stored as `timestamptz` in UTC (ADR-0003). The current
time is supplied by an injected clock so that runs are deterministic
under test.

## Active state

For the MVP, every successfully ingested job is active (ADR-0004).

- Newly inserted jobs are written with `is_active = true`.
- Re-observed jobs are set to `is_active = true`.
- Nothing in the MVP sets `is_active = false`.

Deactivation is deferred. When it is introduced, it may only reason about a
job's absence from a crawl of that company that **succeeded**. A failed
company crawl must never affect the `is_active` or `last_seen_at` of that
company's existing jobs.

## Failure isolation

Failure while fetching or processing one configured company must not
prevent other configured companies from being processed.

The failure must be logged with enough context to identify:

- source
- company
- operation
- error

A company failure has no effect on the stored rows of that company. Its jobs
are neither touched, restamped, nor deactivated.

Every outbound HTTP request must have a bounded timeout. Failure isolation
that relies only on exception handling is insufficient, because a hung
request would stall the entire run.

## Crawl outcome model

Each crawl run records a status:

| Status | Condition |
|---|---|
| success | every attempted company succeeded |
| partial_success | at least one company succeeded and at least one failed |
| failed | at least one company was attempted and none succeeded |

A run over zero enabled companies is `success`.

Each company within a run records its own status (`success` or `failed`), its
counters, and, on failure, the error type and message.

These records are the substrate for future deactivation, which needs to know
which companies were crawled successfully.

## Run reporting

A run reports the following counters, per company and aggregated:

| Counter | Meaning |
|---|---|
| fetched | jobs returned by the source |
| new | jobs inserted for the first time |
| existing | jobs already present and re-observed |
| updated | subset of `existing` whose source-derived fields changed |
| invalid | payload entries rejected during normalization |

`fetched = new + existing + invalid` must hold per company.

**Process exit code is intentionally unresolved.** The MVP exits 0 for
`success`, `partial_success` and `failed` alike. Only an unhandled crash
produces a non-zero exit. Making partial failure non-zero is a separate
decision to be taken when a scheduler consumes the exit code.

## Structured logging

Logs are emitted as structured records. Every failure record carries at
minimum `source`, `company`, `operation` and `error`. Every run emits a
completion record carrying the run status and aggregated counters.

## MVP Acceptance Criteria

Given a configured Greenhouse board containing N jobs:

### First run

Expected:

- fetched = N
- new = N
- existing = 0
- run status = success

### Second run with unchanged source data

Expected:

- fetched = N
- new = 0
- existing = N
- updated = 0
- run status = success

The database must still contain exactly N jobs.

No duplicate records may exist.

Every job's `first_seen_at` is unchanged and every `last_seen_at` has advanced.

### Run with one failing board

Given two configured boards where the first fails and the second returns M jobs:

- the second board's M jobs are persisted
- run status = partial_success
- the failing company's existing rows are byte-for-byte unchanged
- one structured failure record is emitted with source, company, operation, error

## Non-goals

The MVP does not include:

- Lever
- Ashby
- job deactivation
- role, seniority or geography filtering
- LLM matching
- Telegram notifications
- frontend
- authentication
- payments
- monetization
