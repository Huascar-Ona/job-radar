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

Reconciliation happens at the start of every `IngestionService.run()`, before
the run row is created (approved 2026-11-10):

- Every configured board is synced into `companies`, enabled or not, so the
  stored `is_enabled` flag follows the file.
- For every supported source, companies whose board token the file no longer
  lists are disabled, never deleted. A source with no configured boards has
  all of its companies disabled.
- The companies crawled are the enabled ones read back after reconciliation,
  in the deterministic order `CompanyRepository.enabled_companies()` returns.
- Disabled and removed boards are not crawled, get no per-company result for
  the run, and their stored jobs are not modified. Disabling a board is not
  job deactivation (ADR-0004).

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

### Text PostgreSQL cannot store

Approved 2026-11-11. PostgreSQL cannot store the character U+0000 (NUL), not
in a `text` column and not as `\u0000` inside `jsonb`. A normalized
observation therefore rejects a NUL in any text field and in any key or
string value of `raw_data`, however deeply nested. The posting becomes
`invalid` (a `NormalizationError` whose reason names each affected field with
the category `nul_character`), and the company continues.

The value is rejected, never stripped or replaced: storing anything other
than what the source sent would be inventing data. Without this rule a single
such posting would fail its company's whole transaction.

`department` was added to the model so that downstream role filtering has a
first-class column to reason about instead of parsing `raw_data`.

## Greenhouse MVP

The system must:

1. Fetch jobs from configured Greenhouse boards using the public Board API
   with the literal query parameter `content=true` (ADR-0006).
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
| department | `departments[0].name`; null when the array is absent or empty |
| description | `content`, HTML-unescaped **exactly once** |
| job_url | `absolute_url`, verbatim and never host-restricted |
| apply_url | null — applying is a separate authenticated endpoint, not a field |
| source_published_at | `first_published` |
| source_updated_at | `updated_at` |
| raw_data | the unmodified job object as received |

`id` is a JSON number in the source and exceeds 32-bit range in practice
(values above 8.8e9 were observed). It is converted to text on the way in and
never stored numerically.

`first_published` supplies `source_published_at`. Should it be absent or null
for a given job, `source_published_at` is null; it is never backfilled from
`first_seen_at` or from `updated_at`.

## Greenhouse board completeness (`meta.total`)

Approved 2026-11-10 (ADR-0016 addendum). The Board API envelope is
`{"jobs": [...], "meta": {"total": <integer>}}`. `meta.total` is observed, not
documented (see below), so its absence is tolerated; when present, it is
enforced:

| `meta` / `meta.total` in the response | Collector behavior |
|---|---|
| `meta` absent or null | Accepted. Completeness is **not verified** for that response. |
| `meta` present and non-null but not a JSON object (list, string, number, boolean) | `CollectorError`, operation `decode` |
| `meta.total` absent or null | Accepted. Completeness is **not verified** for that response. |
| `meta.total` a JSON integer equal to `len(jobs)` | Accepted. Completeness verified. |
| `meta.total` a JSON integer different from `len(jobs)`, in either direction | `CollectorError`, operation `decode` |
| `meta.total` present but not a JSON integer (boolean, float, string, other) | `CollectorError`, operation `decode` |

No pagination is implemented or attempted. A mismatch fails the company rather
than letting a truncated board be recorded as a successful crawl.

**Known design debt (recorded 2026-11-11).** Whether a response's
completeness was verified is not recorded anywhere. The collector returns the
postings alone, and neither the logs nor `crawl_company_results` distinguish a
verified board from an unverified one. The ADR-0004 addendum requires future
deactivation to account for unverified completeness, so this must be resolved
before deactivation is introduced. Nothing tracks it yet.

## Documented provider guarantees

Taken from the Greenhouse Job Board API documentation, consulted 2026-09-19.
These are the only statements the provider actually makes; everything else in
this section is observation.

- The list endpoint is `GET /v1/boards/{board_token}/jobs` and **requires no
  authentication**.
- `id` identifies the job **post**; `internal_job_id` identifies the **job**.
- `internal_job_id` is **null for prospect posts**.
- `metadata` may be null.
- `content=true` adds `content`, `departments` and `offices`.
- `content` is returned with **HTML entities encoded**.
- Submitting an application is a **separate authenticated endpoint**
  (`POST /v1/boards/{board_token}/jobs/{id}`), not `absolute_url`.

Explicitly **not** documented, and therefore not assumed:

| Question | Status |
|---|---|
| Uniqueness scope of `id` | **Undocumented** (ADR-0014) |
| Stability of `id` across crawls, and behavior on repost | **Undocumented** |
| Pagination | **Inferred absent** — no pagination parameters are documented, and `meta.total` equalled `len(jobs)` in every sample |
| Rate limits | **Unknown** — absent from the documentation, and no `RateLimit` or `Retry-After` headers were returned |
| `first_published` | **Not documented as a field.** It appears only in an official response *example*. Treated as nullable best-effort |
| `company_name` | Not documented as a field; appears in an official example |
| Error response format | Undocumented; see below |

## Observed source behavior

Verified 2026-09-15 against three public boards (458 jobs), and again
2026-09-19 against six public boards (2,610 jobs). Observation, not contract.
See `tests/fixtures/greenhouse/README.md`.

| Observation | Consequence for the MVP |
|---|---|
| `meta.total` equalled `len(jobs)` on every board, up to 882 jobs in one response | No pagination is implemented. A future mismatch between the two is a hard error, not a silent truncation; the exact rules are in "Greenhouse board completeness". |
| `first_published` present and non-null on 2,610/2,610 jobs | `source_published_at` is populated for Greenhouse. Newness is source-relative, not merely radar-relative. The field is nonetheless undocumented, so absence must normalize to null rather than fail. |
| `departments`, `offices` and `content` are returned **only** when `content=true`, and the parameter is matched **literally**: `content=1`, `content=false` and `content=bogus` all omit them | The collector must send the exact string `true`. |
| `departments[]` carries a hierarchy (`parent_id`, `child_ids`). **2,608 of 2,610 jobs had exactly one entry; 2 had none. More than one was never observed** | Empty is a real case and normalizes to null. Only `departments[0].name` is normalized; if several are ever returned, the first is taken. The full array stays in `raw_data`, and a second entry appearing later is evidence for revisiting the normalized representation. |
| `content` is HTML-escaped (`&lt;p&gt;`). **Unescaping twice changes the text on 2,412 of 2,610 jobs** | The normalizer unescapes **exactly once**. Double-unescaping is a defect, not a cosmetic issue: `&amp;lt;` would become `<`. |
| `updated_at` and `first_published` carry varying UTC offsets (`-04:00`, `-05:00`), never `Z` | Offsets must be parsed and converted to UTC, not truncated. |
| `location` is an object whose only key is `name`, non-null on 2,610/2,610, but frequently **compound**: `"San Francisco, CA \| Seattle, WA"`, or a list of a dozen places | `location` is free text carried across verbatim. Any future geographic filtering must parse it, not match it. |
| `offices[]` held 0 to 6 entries | Not used for `location`; kept in `raw_data`. |
| `absolute_url` used five distinct hosts including **customer-owned domains** (`databricks.com`, `stripe.com`, `careers.duolingo.com`) | `job_url` must **not** be host-restricted or pattern-validated. |
| One `internal_job_id` mapped to as many as **14 posts** on a single board | Reinforces that `id` is a post, not a job, and that repost tracking would need `internal_job_id` (ADR-0014, deferred). |
| 266 of 2,610 titles carried leading or trailing whitespace | The domain's `RequiredText` strips; no normalizer-side handling needed. |
| `education` was **absent entirely** on 1,918 jobs and present on 692 | Absent and null are distinct cases. The source schema must tolerate both. |
| **404 returned a JSON body, but a malformed path returned `text/html`** | An error response **cannot be assumed to be JSON**. The collector classifies on status first and must not require a parseable body. |
| `ETag` is returned and `If-None-Match` yields `304` | Conditional requests are viable. Deliberately **out of the MVP** (ADR-0016); recorded as a future optimization with evidence. |
| `company_name` is present in the payload | Ignored for identity and display. Configuration remains authoritative (ADR-0007). It may be used later as a drift check against `display_name`. |
| Jobs with `content=true` averaged ~8.5 KB and peaked at ~23 KB; the board payload grew 79 KB to 1.0 MB (x12.6) | Drives the `content_hash` guard on `raw_data` (ADR-0011). |

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

When an already-known job is observed again by the company it belongs to:

- `first_seen_at` is never modified.
- `last_seen_at` is set to the observation's `observed_at`.
- `is_active` is set to true.
- All source-derived fields are overwritten with the newly observed values.
- `raw_data` is replaced with the newest payload only when `content_hash`
  changed, so unchanged jobs do not rewrite a ~10 KB payload on every crawl
  (ADR-0011).
- A job whose `content_hash` is unchanged counts as `existing` but not
  `updated`. Its row is still updated: `last_seen_at`, `is_active`,
  `updated_at` (the transaction time) and `previous_content_hash` are written,
  and the source-derived fields and `content_hash` are rewritten with
  identical values. Only `raw_data` keeps its stored value rather than being
  replaced.
- `previous_content_hash` records the hash the row held before this update.
  The upsert classifies its own outcome from it (ADR-0015).

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

A company whose every posting was `invalid` still records `success`
(ADR-0016). Future deactivation must not treat such a crawl, or one whose
completeness was not verified, as proof that the company's jobs disappeared
(ADR-0004 addendum).

## Ingestion run

Approved 2026-11-10. `IngestionService.run()` performs, in order:

1. **Reconcile configuration** in its own transaction (see "Configuration").
   Before that transaction commits, every enabled company's source is checked
   against the wired collectors. An enabled company whose source has no
   collector raises `ValueError`: reconciliation rolls back, no run row is
   opened and no company is fetched. This is a wiring error in the caller,
   not a crawl failure.
2. **Start the run**: take `observed_at` once from the injected clock, open the
   `crawl_runs` row in the `running` state, and commit it.
3. **Crawl each enabled company sequentially**: fetch through the collector
   registered for its source, then normalize and upsert each posting
   occurrence in response order inside the company's transaction. A successful
   company's result row is written **in that same transaction**, so its jobs
   and its success record commit or roll back together.
4. **Finish the run**: derive the status, write aggregated counters, commit,
   and emit the completion log record.

One `observed_at` is used for every job in the run. Per-company `started_at`
and `finished_at` are read from the same injected `Clock` as timing metadata
only; they are not observation times and do not change the run's single
`observed_at`.

The run's `finished_at` is also read from the injected `Clock`, once, when
the run is finished.

A run over zero enabled companies still reconciles, opens and finishes its
run row, and records `success` with zero counters.

Failures outside any company's processing are not company failures. A
`PersistenceError` while reconciling configuration, opening the run row or
finishing it propagates out of `run()`: before the run row exists there is
nothing to record, and a run that cannot be finished remains `running`
(see "Crawl outcome model").

The service is source-agnostic: it depends on the `Collector` and
`Normalizer` protocols and on the domain model only. Source-specific wiring
belongs to the caller. There are no retries (ADR-0016) and no concurrent
company processing (ADR-0009) in the MVP.

## Failure isolation

Failure while fetching or processing one configured company must not
prevent other configured companies from being processed.

The failure must be logged with enough context to identify:

- source
- board_token
- display_name
- operation
- error (safe details only; see "Safe error messages" and "Persistence errors")

A company failure has no effect on the stored rows of that company. Its jobs
are neither touched, restamped, nor deactivated.

Every outbound HTTP request must have a bounded timeout. Failure isolation
that relies only on exception handling is insufficient, because a hung
request would stall the entire run.

### Failure classes (approved 2026-11-10)

| Failure | Effect |
|---|---|
| `NormalizationError` for one posting | That occurrence counts as `invalid`; the company continues and can still succeed |
| `CollectorError` | The company fails; no job is written for it |
| `PersistenceError` while processing the company | The company transaction rolls back; the company fails |
| Recording a failed company's result also fails | **The run aborts**: no further companies are attempted, the run is not finished, and the error propagates. The run row may remain `running` |
| Any other exception (a programming error) | Propagates and crashes the run; it is never recorded as a company failure |

A failed company's result is recorded in a **new** transaction, after its
own transaction has rolled back, with status `failed`, zero counters, and its
error type and safe message.

### Safe error messages

Approved 2026-11-10. Error messages that are logged or persisted are safe by
construction, not by truncation. They are assembled only from fixed text,
HTTP status codes, field names, validation categories, Python type names and
integer counts. Source content is untrusted and never quoted.

`CollectorError` messages:

| Failure | Message |
|---|---|
| Non-200 response | `HTTP <status>`; the response body is never included |
| Timeout | `request timeout` |
| Any other transport failure | `transport error (<httpx exception class name>)` |
| Body that is not valid JSON | `response is not valid JSON`; no decoder text |
| Envelope shape (`jobs`, `meta`, `meta.total`) | The key and the actual JSON type, or the two counts; a `meta.total` beyond 10^9 is reported as out of range rather than quoted |

The `CollectorError` is raised without the exception that caused it: it has
neither a `__cause__` nor a `__context__`. Its `source`, `company` and
`operation` context is unchanged.

`NormalizationError` reasons name the failing field, the validation category
and the actual type, e.g. `id: int_type (got str)`, `title: missing` or
`updated_at: timestamp has no timezone offset`. They never include a source
value (timestamp excerpts included), `raw_data`, or Pydantic's own message
text, which can quote input. A location segment that is not a plain field
name is replaced. The `ValidationError` or parsing error is not chained.

The ingestion service still bounds every logged or persisted message to 500
characters, as a backstop rather than as the sanitization.

### Persistence errors

The persistence adapter translates every SQLAlchemy failure into
`PersistenceError` before it reaches the ingestion service. SQLAlchemy and
driver exception text can contain SQL, bound parameters and row content,
including `raw_data`; none of it may reach a log record or a persisted error
message. Therefore:

- The safe message is built only from the exception category (the
  SQLAlchemy exception class name, such as `IntegrityError` or
  `OperationalError`) and, when the driver provides one, the SQLSTATE code.
  It is bounded in length.
- No part of the original exception's message is used, **including its first
  line**, which can itself contain bound values or row content.
- The original exception is not chained into the raised `PersistenceError`
  and is not logged in normal company-failure reporting.
- `crawl_company_results.error_type` records the Job Radar error class
  (`CollectorError` or `PersistenceError`) and `error_message` only its
  sanitized safe message, never the original exception text.
- Opening a session, every repository call and the commit are translated
  alike. A non-SQLAlchemy exception from any of them is a programming error
  and propagates unchanged.

Each `PersistenceError` names the transaction it failed in as its
`operation`:

| Operation | Transaction |
|---|---|
| `reconcile_config` | Configuration reconciliation |
| `start_run` | Opening the run row |
| `persist_company` | A company's job writes and success record |
| `record_company_failure` | A failed company's result |
| `finish_run` | Finishing the run row |

A failure of the commit itself adds the suffix `_commit`, e.g.
`persist_company_commit`.

## Crawl outcome model

Each crawl run records a status:

| Status | Condition |
|---|---|
| success | every attempted company succeeded |
| partial_success | at least one company succeeded and at least one failed |
| failed | at least one company was attempted and none succeeded |

A run over zero enabled companies is `success`.

Each company within a run records its own status (`success` or `failed`), its
counters, and, on failure, the error type and message. A failed company
records **zero** counters, because none of its writes were committed; what it
had fetched or attempted before failing appears only in its log record.

These records are the substrate for future deactivation, which needs to know
which companies were crawled successfully.

A run interrupted by an abort or a crash may remain in the `running` state.
No recovery mechanism exists yet (ADR-0008 addendum).

## Run reporting

A run reports the following counters, per company and aggregated:

| Counter | Meaning |
|---|---|
| fetched | posting occurrences returned by the source |
| new | occurrences whose upsert inserted the job |
| existing | occurrences whose upsert re-observed a job already stored for this company |
| updated | subset of `existing` whose source-derived fields changed |
| invalid | occurrences rejected during normalization, or whose identity belongs to a different company |

`fetched = new + existing + invalid` must hold per company.

Counting rules (approved 2026-11-10):

- Counters count **posting occurrences**, not distinct ids. Each fetched
  occurrence contributes to exactly one of `new`, `existing` or `invalid`.
- `updated` is a subset of `existing`; `updated <= existing` always holds.
- A `COMPANY_CONFLICT` upsert outcome (ADR-0014, ADR-0015) counts as
  `invalid`, never as `existing`, leaves the stored row unchanged, and emits a
  structured `ERROR` log record carrying both company ids. The company can
  still succeed. Conflicts are distinguishable from normalization rejections
  only in the logs.
- **Duplicate ids within one response** are not deduplicated. Occurrences are
  processed in response order under the normal upsert rules, all with the
  run's single `observed_at`:
  - the first occurrence counts by what it finds stored (`new`, or
    `existing`/`updated`);
  - a later identical occurrence counts as `existing`;
  - a later changed occurrence counts as `existing` and `updated`, and its
    content replaces the earlier one's;
  - an occurrence that fails normalization counts as `invalid` and changes
    nothing.

  The last successfully normalized occurrence therefore determines the stored
  content. A job new to this run that appears twice counts once as `new` and
  once as `existing`.
- A failed company records zero for every counter (see "Crawl outcome
  model").

**Process exit code is intentionally unresolved.** The MVP exits 0 for
`success`, `partial_success` and `failed` alike. Only an unhandled crash
produces a non-zero exit. Making partial failure non-zero is a separate
decision to be taken when a scheduler consumes the exit code.

## Structured logging

Logs are emitted as structured records. Every failure record carries at
minimum `source`, `board_token`, `display_name`, `operation` and safe `error`
details; posting-level records also carry `source_job_id` when it is known.
Every run emits a completion record carrying the run status and aggregated
counters.

Domain errors expose part of this context through `log_context()`
(`CollectorError` reports the board token under `company`;
`NormalizationError` carries no company at all). The ingestion service adds
the missing company fields when it logs them.

## MVP Acceptance Criteria

Given a configured Greenhouse board containing N jobs with distinct ids, all
of which normalize successfully (duplicates and invalid postings follow
"Run reporting"):

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
- one structured failure record is emitted with source, board_token,
  display_name, operation and safe error details
- the failing company's result row records `failed` with zero counters

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
