# ADR-0005: No filtering during ingestion

- Status: Accepted
- Date: 2026-09-15

## Context

The product goal targets software engineering roles suitable for developers in
Mexico and LATAM. The tempting shortcut is to filter inside the Greenhouse
collector so only relevant jobs are stored.

## Decision

Ingestion persists every job published on a configured board.

Role, seniority, geography and matching filters are downstream concerns
applied at query or matching time. Collectors contain no filtering logic.

`department` is added to the normalized model so downstream filtering has a
first-class column rather than having to parse `raw_data`.

## Consequences

- Filter criteria can change without re-crawling, because the raw corpus is
  already stored. Filtering at ingestion would make every criteria change a
  backfill.
- Collectors stay purely about transport and translation, which keeps the
  source boundary honest and makes Lever and Ashby cheap to add.
- Storage grows faster, since irrelevant postings are retained. Accepted.
- `fetched` counts everything on the board, so it is not a measure of product
  relevance.
