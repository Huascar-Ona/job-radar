# ADR-0006: Greenhouse public Board API

- Status: Accepted
- Date: 2026-09-15

## Context

Greenhouse exposes an unauthenticated public Job Board API and an
authenticated Harvest API. `description` is a required normalized field, and
the board listing endpoint omits job content unless explicitly requested.

## Decision

Use the public Board API:

```
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
```

No credentials are required. Requests send an identifying User-Agent.

This follows the project principle of preferring official public APIs over
HTML scraping.

## Consequences

- No credential management or per-customer onboarding for the MVP.
- `content=true` is **required, not optional**: `departments`, `offices` and
  `content` are absent without it, and `description` and `department` are
  required normalized fields.
- Response size grows roughly 12x with `content=true` (79 KB to 1.0 MB for 83
  jobs; ~10 KB per job). This drives the `content_hash` write guard
  (ADR-0011).
- The endpoint is unpaginated in practice, so one request yields a whole board.
- The MVP is limited to what the Board API exposes. `apply_url` has no
  counterpart and stays NULL.

## Verification

Performed 2026-09-15 against three public boards (458 jobs total). Sanitized
fixtures are in `tests/fixtures/greenhouse/`; the full observation table is in
SPEC.md under "Observed source behavior".

The material correction: this endpoint **does** return `first_published`,
non-null on every one of the 458 sampled jobs. An earlier draft assumed no
publication timestamp existed and specified `source_published_at` as
permanently NULL for Greenhouse. That was wrong; the field is now mapped
(ADR-0003).

`first_published` is treated as nullable regardless, since its stability is
observed rather than contractually guaranteed by published documentation,
which was not consulted. A job lacking it normalizes to NULL rather than
failing.

## Addendum, 2026-09-19: documentation consulted

The section above notes that published documentation "was not consulted". It
has been since, ahead of implementing the collector. The decision to use the
public Board API with `content=true` is unchanged. What the documentation adds:

- The endpoint and its lack of authentication are **documented**, as is the set
  of fields `content=true` adds and the fact that `content` carries HTML
  entities encoded.
- **`first_published` is not a documented field.** It appears only in an
  official response *example*. That is stronger than pure observation and
  weaker than a guarantee, so the nullable treatment above stands for a better
  reason than it was originally given.
- **Pagination remains inferred, not documented.** No pagination parameters are
  described; `meta.total` matched `len(jobs)` on every board sampled, up to 882
  jobs.
- **Rate limits remain unknown.** Absent from the documentation, and no
  `RateLimit` or `Retry-After` headers are returned. This absence is what makes
  a retry policy unjustifiable today (ADR-0016).
- **Identifier uniqueness and stability remain undocumented**, as recorded in
  ADR-0014.
- **`content=true` is matched literally.** `content=1`, `content=false` and
  `content=bogus` all return a response without `content`, `departments` or
  `offices`.
- **Error responses are not reliably JSON.** A missing board returns
  `{"status":404,"error":"Job not found"}`, but a malformed path returns
  `text/html`. The collector must classify on status and never require a
  parseable body.
- Applying is a **separate authenticated endpoint**, which is why `apply_url`
  is null rather than a copy of `absolute_url`.

A second sample on the same date covered six boards and 2,610 postings; the
full table is in SPEC.md. One earlier claim did not survive it: `departments`
can be empty, so "every sampled job had exactly one entry" is no longer true.
