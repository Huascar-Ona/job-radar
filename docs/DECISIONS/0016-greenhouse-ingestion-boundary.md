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
