# ADR-0016: Greenhouse ingestion boundary, error taxonomy and retry policy

- Status: Accepted
- Date: 2026-09-19

## Context

Before implementing the first external integration, the split between collector
and normalizer had to be fixed, along with what each failure means and whether
failed requests should be retried. The Board API contract was verified against
official documentation and six public boards (2,610 postings); the evidence is
recorded in SPEC.md and in the addendum to ADR-0006.

## Decision

### Responsibility boundary

The **collector** owns transport and provider retrieval: URL construction from
a board token, the literal `content=true` parameter, one GET with a bounded
timeout and an identifying User-Agent, status classification, JSON decoding,
and verifying the response envelope (`jobs` present and a list). It returns
provider payloads unchanged.

The **normalizer** owns translation: one provider posting to one
`NormalizedJob`. It performs no I/O.

Neither builds observation times, writes to the database, filters postings
(ADR-0005), or owns a transaction.

Envelope validation belongs to the collector because it asks whether the
response is a Job Board API response at all, not whether a posting is valid.

### Error taxonomy

`CollectorError` — the provider did not deliver a usable board: DNS or connect
failure, timeout, any non-200 status, an unparseable body, a missing `jobs`
key, or `jobs` not being a list.

`NormalizationError` — the board arrived and one posting cannot be converted:
missing or non-integer `id`, missing or blank `title`, missing `absolute_url`,
or a timestamp that is present but unparseable. Absent optional fields are
never an error.

**A malformed posting does not invalidate the rest of the board.** It counts as
`invalid` for that company while the company still succeeds; only a
`CollectorError` fails it.

Unknown provider fields are tolerated at the boundary. `NormalizedJob` keeps
`extra="forbid"`: tolerant at the edge, strict in the domain.

### Retries: none in the MVP

No HTTP retry, for any failure class.

**This records an absence of evidence, not a permanent prohibition.** Rate-limit
behavior is undocumented and no `RateLimit` or `Retry-After` headers are
returned, so any backoff would be invented rather than derived. Crawling is
sequential (ADR-0009), so a retry multiplies the worst case for the whole run,
and per-company failure isolation already bounds the damage.

The next scheduled crawl is the retry; cadence is the dial, not backoff.

When revisited, the classes differ:

| Class | If retries are introduced |
|---|---|
| Timeout, connection failure, 429, 5xx | Candidates — transient, and `crawl_company_results` will show their real frequency |
| 404 and other 4xx | **Never.** A missing board is a configuration error; retrying hides it |
| Malformed provider response | **Never.** Not transient |

`crawl_company_results` accumulates exactly the evidence a real policy needs.

### ETag and conditional requests: out of the MVP

`ETag` is returned and `If-None-Match` yields `304`, verified empirically. Not
adopted: it requires durable per-board state and a code path that never sees
job data, for no benefit at current board counts. Recorded as a future
optimization that is known to work rather than assumed to.

## Consequences

- A failure's type says which half of the integration broke, before reading any
  message.
- The ingestion service must catch normalization failures **per posting**, not
  per board, or the `invalid` counter is unreachable. This constrains its
  design and is the reason it is recorded here rather than later.
- A transient failure costs one crawl cycle of staleness for that board. This
  is the accepted cost of having no retry, and it is the thing to measure
  before adding one.
- Adding a retry policy later needs no structural change: the error classes
  already distinguish retryable from not.

## Addendum, 2026-11-10: completeness, persistence errors and counting

Approved ahead of implementing the ingestion service. The decisions above are
unchanged; this extends the taxonomy. SPEC.md holds the operational detail
("Greenhouse board completeness", "Ingestion run", "Failure isolation",
"Run reporting").

### `meta.total` completeness check (collector)

The envelope check gains one rule. `meta.total` is observed on every sampled
board but is not documented, so:

- `meta` absent or null, or `meta.total` absent or null: accepted, and
  completeness is **not verified** for that response.
- `meta` present and non-null but not a JSON object: a malformed envelope,
  `CollectorError` with operation `decode`.
- `meta.total` present: it must be a strict JSON integer (not a boolean,
  float or string) and must equal `len(jobs)`. A mismatch or a malformed
  value is a `CollectorError` with operation `decode`.

No pagination is introduced. A board that disagrees with its own count is a
failed crawl, never a silently truncated successful one: recording a
truncated board as `success` would later look like jobs disappearing
(ADR-0004).

### `PersistenceError`

A third error class: the database could not complete a write or read the
ingestion service needed. The persistence adapter translates every
SQLAlchemy failure into it before it reaches the service, which therefore
never depends on SQLAlchemy.

SQLAlchemy and driver exception text can carry SQL statements, bound
parameters and row content, `raw_data` included, and **even the first line
of a driver message can contain bound values or row data**. The safe message
is therefore built only from the exception category (the SQLAlchemy class
name) and, when available, the SQLSTATE code, and is bounded in length. No
text from the original exception is used, the original is not chained, and it
is not logged in normal company-failure reporting. A failed company's result
records the class name (`CollectorError` or `PersistenceError`) as
`error_type` and only the sanitized safe message as `error_message`.

Every database call the adapter makes is translated this way: opening the
session, each repository call and the commit. The error's `operation` names
the transaction (`reconcile_config`, `start_run`, `persist_company`,
`record_company_failure`, `finish_run`), with the suffix `_commit` when the
commit itself fails. A non-SQLAlchemy exception is a programming error and
propagates unchanged.

### Safe source error messages

The same reasoning applies to the source side. Provider error bodies,
transport exception text and decoder messages are untrusted content, and so
are posting values. Truncating them bounds their size but not what they say,
so collector and normalizer messages are **safe by construction**:

- `CollectorError` messages use only fixed text, the HTTP status code, an
  httpx exception class name, JSON type names and counts: `HTTP 503`,
  `request timeout`, `transport error (ConnectError)`,
  `response is not valid JSON`. No response body or exception text is quoted,
  and the causing exception is not chained.
- `NormalizationError` reasons use only field locations, Pydantic validation
  categories and actual type names, e.g. `id: int_type (got str)`. Source
  values, including timestamp excerpts, and Pydantic's message text are never
  quoted, and the causing exception is not chained.

The cost is diagnostic detail: an error body or a rejected value is no longer
visible in the logs. The status code, field and type identify the failure
class, and `raw_data` keeps every successfully normalized posting.
The ingestion service's 500-character bound on messages remains as a
backstop. SPEC "Safe error messages" lists the messages.

### Failure classes during a run

The Decision's "only a `CollectorError` fails it" contrasts the collector with
the normalizer. Across the whole run, a recoverable `PersistenceError` fails a
company too:

| Failure | Classification |
|---|---|
| `NormalizationError` | One `invalid` occurrence; the company continues |
| `CollectorError` | Recoverable company failure |
| `PersistenceError` while processing a company | Recoverable company failure, provided its failure record can be persisted in a new transaction |
| Persisting that failure record also fails | The run aborts and may remain `running` (ADR-0008 addendum) |
| Any other exception | A programming error: it propagates and is never recorded as a company failure |

### Counting

- Each fetched posting occurrence counts exactly once as `new`, `existing` or
  `invalid`; `updated` is a subset of `existing`.
- `COMPANY_CONFLICT` counts as `invalid`, never `existing`, with a structured
  `ERROR` log record. It keeps `fetched = new + existing + invalid` true
  without a new counter or schema change; the cost is that conflicts are
  distinguishable from normalization rejections only in the logs.
- Duplicate ids within one response are not deduplicated; they are processed
  in response order and the last successfully normalized occurrence
  determines the stored content.
- A board whose every posting is `invalid` still **succeeds**, as decided
  above. The risk this creates for future deactivation is recorded in the
  ADR-0004 addendum.
