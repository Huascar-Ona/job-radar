# ADR-0003: Timestamp semantics

- Status: Accepted
- Date: 2026-09-15

## Context

Source timestamps and observation timestamps answer different questions.
Conflating them silently corrupts any future "how fresh is this job" logic,
and the corruption is undetectable after the fact.

## Decision

Four distinct fields, never substituted for one another:

- `source_published_at` — publication time reported by the source
- `source_updated_at` — update time reported by the source
- `first_seen_at` — first Job Radar observation
- `last_seen_at` — most recent Job Radar observation

All are `timestamptz` stored in UTC.

Unavailable source timestamps remain NULL.

The current time is supplied by an injected clock, not by scattered `now()`
or `CURRENT_TIMESTAMP` calls.

## Consequences

- One crawl stamps a single consistent instant across every job it touches.
- Tests can assert exact timestamp behavior without sleeping or tolerancing.
- Greenhouse does supply a publication time via `first_published`, verified
  present on 458/458 sampled jobs (ADR-0006), so `source_published_at` is
  populated and "newness" is source-relative rather than merely
  radar-relative. An earlier draft of this ADR assumed the field was
  unavailable; live verification disproved that.
- Where a source genuinely omits a publication time, the field stays NULL and
  downstream logic must tolerate it. Substituting `first_seen_at` remains
  forbidden in all cases.
