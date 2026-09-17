# ADR-0009: Sequential crawling with bounded timeouts

- Status: Accepted
- Date: 2026-09-15

## Context

The product goal says "as quickly as possible", which argues for concurrency.
But the MVP crawls a handful of boards against a free public API with unclear
rate limits, and concurrency multiplies both the block risk and the number of
failure modes under test.

Separately, failure isolation implemented only with `try/except` does not
protect against a board that accepts the connection and never responds.

## Decision

Companies are crawled sequentially in the MVP.

Every outbound HTTP request carries a bounded timeout covering connect and
read. Requests send an identifying User-Agent. Retries, if added, are bounded
and use backoff.

## Consequences

- Run duration is the sum of per-board durations. Acceptable at MVP board
  counts; revisit when it stops being acceptable.
- A hung board costs one timeout, not the whole run.
- Concurrency is a later optimization. `httpx` was chosen partly because it
  supports async without replacing the client library.
