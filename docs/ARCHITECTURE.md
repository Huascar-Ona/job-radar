# Job Radar Architecture

## Overview

Job Radar is a multi-source job ingestion system.

External job sources are isolated behind collectors and normalized
into a common internal Job representation before persistence.

## Technology

Python 3.13, PostgreSQL, SQLAlchemy 2, Alembic, httpx, Pydantic v2,
pytest, Ruff, mypy, Docker Compose (ADR-0001).

FastAPI is not part of the first vertical slice. It will be introduced when
an HTTP API is actually needed.

## Initial Architecture

```
        CLI `job-radar ingest` (composition root; owns engine and HTTP client)
                                  |
                                  v
                          IngestionService
      (orchestration, failure isolation, crawl run accounting)
           |                      |                            |
           v                      v                            v
     BoardsConfig        Collector, Normalizer           StoreFactory
  (config/boards.yml)    (per source, injected)       (ingestion ports)
                                  |                            |
                                  v                            v
                         GreenhouseCollector        SqlAlchemyStoreFactory
          httpx, bounded timeout -> Greenhouse     (one transaction per use;
                                  |                 SQLAlchemy failures become
                                  v                 PersistenceError)
                         Greenhouse payload                    |
                  (validated by Pydantic source schema)        v
                                  |                    CompanyRepository
                                  v                    JobRepository ----+
                        GreenhouseNormalizer           CrawlRepository   |
                                  |                                      |
                                  v                                      v
                  NormalizedJob (Pydantic, source-agnostic)    PostgreSQL
                  passed by the service to JobRepository.upsert
                  (INSERT ... ON CONFLICT DO UPDATE)
```

Where each part lives:

| Component | Module |
|---|---|
| CLI: parsing, wiring, run summary, JSON Lines logging | `job_radar.cli` |
| `IngestionService`, crawl summaries | `job_radar.ingestion.service`, `job_radar.ingestion.summary` |
| `StoreFactory` and the store protocols | `job_radar.ingestion.ports` |
| `SqlAlchemyStoreFactory` | `job_radar.persistence.ingestion_store` |
| `CompanyRepository`, `JobRepository`, `CrawlRepository` | `job_radar.persistence.*_repository` |
| `GreenhouseCollector`, `GreenhouseNormalizer` | `job_radar.collectors.greenhouse` |
| `BoardsConfig` | `job_radar.config.boards` |

## Architectural Boundary

Source-specific behavior must remain inside its collector and normalizer.

```
Greenhouse ---\
Lever ---------> NormalizedJob ---> Application
Ashby --------/
```

The rest of the application must not depend on Greenhouse-specific
data structures.

Concretely:

- A collector owns transport: URLs, query parameters, auth, retries, timeouts.
- A normalizer owns translation: source payload -> `NormalizedJob`.
- `Normalizer` is an interface with one implementation per source. There is
  no shared normalizer that branches on source, because such a branch is
  exactly the coupling this boundary forbids.
- Greenhouse Pydantic schemas live in the Greenhouse package and are imported
  by nothing outside it. This is enforced by an automated import test, not
  by convention alone.
- Collectors contain no filtering, ranking or matching logic (ADR-0005).

## Persistence

Jobs are uniquely identified by:

(source, source_job_id)

The database must enforce this invariant with a UNIQUE constraint.

`source_job_id` is stored as text so that providers using UUIDs or opaque
identifiers need no schema change (ADR-0002).

Writes use `INSERT ... ON CONFLICT (source, source_job_id) DO UPDATE` so that
idempotency is enforced by the database and remains correct under concurrent
execution. The application never performs a read-then-write upsert.

`first_seen_at` is protected at the SQL level: the conflict branch never
assigns it. Both observation-history columns are written from the observation's
`observed_at`; `observed_at` itself is not a column.

A job's `source` must equal the `source` of the company it references. Two
columns that must agree get a constraint that makes them agree: `jobs` holds a
composite foreign key `(company_id, source) -> companies (id, source)`, which
requires `UNIQUE (id, source)` on `companies`. RESTRICT applies on both delete
and update, so a company with jobs can neither be removed nor have its source
changed underneath them. Job identity remains `(source, source_job_id)`.

`created_at` and `updated_at` on `companies` and `jobs` are not maintained by
a database trigger. An insert takes their server default, `now()`, and every
repository statement that updates one of those rows assigns
`updated_at = now()` explicitly in its SQL. A raw SQL `UPDATE` outside a
repository therefore does not refresh `updated_at`. This is accepted
deliberately: every production write goes through a repository, and a
trigger would add schema surface that is invisible in the models and easy to
forget when migrating. These columns are operational metadata for debugging,
never the basis of any invariant — the observation history that matters lives
in `first_seen_at` and `last_seen_at`, which are written explicitly.

Each job row carries a `content_hash` over its source-derived fields,
`raw_data` included. When the hash is unchanged, the conflict branch keeps the
stored ~10 KB `raw_data` value instead of replacing it (ADR-0011). The row is
still updated: `last_seen_at`, `is_active`, `updated_at` and
`previous_content_hash` are written, and the other source-derived fields are
rewritten with identical values. SPEC, "Update semantics on re-observation",
has the full list.

## Timestamps

Source timestamps and Job Radar timestamps represent different concepts:

- source_published_at: publication time reported by the source, when available
- source_updated_at: last update reported by the source
- observed_at: instant a normalizer observed the job, taken once per crawl from
  the injected clock. A domain value, not a stored column.
- first_seen_at: first time Job Radar observed the job (stored)
- last_seen_at: most recent time Job Radar observed the job (stored)

`NormalizedJob` carries `observed_at` and neither of the stored columns. A
normalizer sees one payload and cannot know whether that job is new to Job
Radar; asking it to produce `first_seen_at` would be asking it to guess.
Persistence resolves the question, because only the database knows whether the
row already existed:

| Column | INSERT | ON CONFLICT UPDATE |
|---|---|---|
| first_seen_at | `observed_at` | preserved, never assigned |
| last_seen_at | `observed_at` | `observed_at` |

Missing source timestamps must remain null. They must never be inferred
from Job Radar timestamps.

All timestamps are `timestamptz` in UTC. The current time enters the system
through an injected clock, never through scattered `now()` calls, so that a
single crawl stamps one consistent instant and tests are deterministic
(ADR-0003). The domain exposes the `Clock` protocol and `SystemClock`; test
doubles live in the test suite, so no clock that never moves ships in the
application package.

## Reliability

Failure while crawling one company must not stop processing other
configured companies.

Isolation is structural, not incidental:

- Each company is crawled inside its own transaction boundary. A company that
  fails leaves no partial writes and does not roll back other companies.
- Every HTTP request carries a bounded timeout, so a hung board cannot stall
  the run.
- A company failure never writes to that company's existing job rows.

Companies are crawled sequentially in the MVP (ADR-0009). Concurrency is a
later optimization that must be weighed against source rate limits.

**The MVP performs no HTTP retries** (ADR-0016). This records an absence of
evidence, not a permanent prohibition: no rate-limit behavior is documented or
observable, so any backoff would be invented. The next scheduled crawl is the
retry, and `crawl_company_results` accumulates the evidence that would justify
a real policy.

## Collector and normalizer responsibilities

The two halves of a source integration answer different questions, and the
error each raises says which half failed.

| | Collector | Normalizer |
|---|---|---|
| Knows | transport, provider endpoints | provider field semantics, the normalized contract |
| Never knows | Job Radar domain semantics | HTTP, persistence |
| Does | build the URL, GET with a bounded timeout, classify status, decode JSON, verify the response envelope (including its completeness count, where the provider reports one), return provider payloads | translate one provider posting into a `NormalizedJob` |
| Never does | build `NormalizedJob`, decide observation times, write to the database, filter postings | perform I/O of any kind |
| Raises | `CollectorError` — the provider did not deliver a usable board | `NormalizationError` — the board arrived but one posting cannot be converted |

The envelope check sits with the collector because it asks whether the
response is a Job Board API response at all, not whether any posting is valid.

**A malformed posting must not invalidate the rest of the board.** One
`NormalizationError` counts as `invalid` for that company; the company still
succeeds. Of the two halves, only a `CollectorError` fails it; outside them, a
recoverable `PersistenceError` also does (ADR-0016 addendum). This is what
makes the `invalid` counter reachable, and it constrains the ingestion
service: it must catch normalization failures per posting rather than per
board.

Unknown provider fields are tolerated at the collector boundary — providers add
fields — while `NormalizedJob` forbids extras. Tolerant at the edge, strict in
the domain.

## Crawl outcomes

Crawl outcomes are first-class persisted data, not just log lines
(ADR-0008).

- A `crawl_run` row records the run: status (`success`, `partial_success`,
  `failed`), timing, and aggregated counters.
- A `crawl_company_result` row records each company within the run: status,
  counters, and error details on failure.

This exists because deferred deactivation (ADR-0004) may only reason about a
job's absence from a **successful** crawl of its company. Without a durable
record of which crawls succeeded, a future "job disappeared" rule would
deactivate jobs whose board merely failed to respond. That failure mode is
silent and unrecoverable, so the record is built now even though nothing
consumes it yet.

Raw source responses are retained per job for debugging.

## Configuration

Enabled boards are declared in `config/boards.yml` under version control, with
`source`, `board_token`, `display_name` and `enabled` per entry (ADR-0012).
The file is parsed and validated by Pydantic at startup, then reconciled into
the `companies` table at the start of each run.

The board token is the stable identity; the display name is mutable metadata
that may be edited without affecting job identity (ADR-0007). The Greenhouse
payload also carries `company_name`, which is deliberately ignored:
configuration stays authoritative.

Database-managed configuration and an admin UI are out of scope for the MVP.

## Transaction ownership

Repositories never commit and never roll back. They execute statements on a
`Session` handed to them and report what happened; the caller decides whether
the work stands. A test enforces this by scanning the repository modules for
`.commit()` and `.rollback()`.

Ownership by layer:

| Layer | Owns |
|---|---|
| CLI | the process-level session/engine lifecycle |
| IngestionService | **one transaction per company**, plus the run-level writes |
| Repositories | no transaction; statements only |

Per-company ownership is what makes failure isolation structural rather than
incidental: a company that fails rolls back its own partial writes and leaves
every other company's work committed. A repository that committed internally
would break that, because a later failure in the same company could no longer
be undone.

The run row is opened before the first company and closed after the last, so
it is committed independently of any single company's transaction.

Transactions within one run (approved 2026-11-10; SPEC, "Ingestion run"):

1. Configuration reconciliation, including the check that every enabled
   company's source has a collector wired. A wiring error raises
   `ValueError` before this transaction commits, so it rolls back.
2. Opening the run row in the `running` state.
3. One per company: its job writes and, on success, its result row. On
   failure it rolls back, and the failed result is written in a further,
   separate transaction.
4. Finishing the run row.

If the separate failure-record transaction also fails, the run aborts and may
remain `running`. A `PersistenceError` in transactions 1, 2 or 4 is not a
company failure: it propagates out of the run.

The persistence adapter, not the ingestion service, translates SQLAlchemy
failures into `PersistenceError`, whose message never contains SQL, bound
parameters, row content or driver message text (ADR-0016 addendum).

## Testing boundaries

- Normalizers are pure and tested against captured payload fixtures in
  `tests/fixtures/greenhouse/`, whose shape was captured from a live Board API
  response and whose identifying values are synthetic.
- Collectors are tested against a stubbed HTTP transport, never live network.
- Anything asserting database behavior runs against real PostgreSQL. SQLite is
  not an acceptable substitute because the design depends on `ON CONFLICT`,
  `jsonb` and `timestamptz` semantics (ADR-0010).
- The ingestion pipeline is tested end to end below the CLI: the real
  `IngestionService`, collector, normalizer, store adapter, repositories and
  PostgreSQL, with only HTTP stubbed (`tests/integration/test_ingestion_pipeline*.py`).
  Most of these tests run inside one transaction that is always rolled back,
  with the service's transactions as SAVEPOINTs within it; persistence
  failures are injected by temporary PostgreSQL triggers created in that
  transaction. Two durability tests commit for real, on identifiers no other
  test uses, and delete exactly those rows afterwards.
- Acceptance tests drive the ingest command end to end through
  `job_radar.cli.run_ingest`, with only the HTTP transport stubbed and the
  clock fixed, against the schema test database
  (`tests/integration/test_cli_acceptance.py`). The CLI commits for real, so
  they use the durability tests' isolation: unique identifiers, a check
  before writing, and a cleanup of exactly those rows.
- The CLI's own decisions (parsing, exit codes, summary, log format, resource
  release) are unit-tested with the ingestion service replaced by a fake
  (`tests/unit/test_cli.py`).

## Current Scope

Initial source:

- Greenhouse

Future sources:

- Lever
- Ashby

Out of scope for the first milestone:

- job deactivation
- filtering and matching
- LLM matching
- Telegram notifications
- frontend
- authentication
- monetization
