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
