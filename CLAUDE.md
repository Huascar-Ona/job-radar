# Job Radar

Job Radar is a multi-source job ingestion and matching system.

## Goal

Discover software engineering jobs suitable for developers in
Mexico/LATAM as quickly as possible.

The first supported source is Greenhouse.

## Project documentation

Before implementing features, read:

- docs/SPEC.md
- docs/ARCHITECTURE.md
- relevant ADRs under docs/DECISIONS/

The specification defines expected behavior.
Architecture documents define system boundaries.

Do not silently change either.

## Engineering principles

- Prefer official/public APIs over HTML scraping.
- Keep source-specific logic isolated behind collectors.
- Normalize external data before persistence.
- External data must never be trusted blindly.
- Crawling one company failing must not stop other companies.
- Job ingestion must be idempotent.
- Never invent unavailable source data.
- Preserve raw source payloads for debugging.

## Development workflow

Before coding:

1. Understand the requested change.
2. Read the relevant specification.
3. Inspect the existing implementation.
4. Identify affected tests.

During implementation:

1. Make the smallest coherent change.
2. Keep source-specific behavior isolated.
3. Add or update tests.
4. Do not weaken tests to make implementation pass.

Before finishing:

1. Run formatting and linting.
2. Run tests.
3. Verify acceptance criteria.
4. Report what changed.
5. Report any unresolved issues.

## Important invariants

A job is uniquely identified by:

(source, source_job_id)

Running a collector repeatedly against unchanged source data
must never create duplicate jobs.

Source timestamps and Job Radar timestamps are different concepts.

Never substitute first_seen_at for source_published_at.

## Current scope

Supported:

- Greenhouse

Not yet supported:

- Lever
- Ashby
- LLM matching
- Telegram alerts
- Web frontend
- Authentication
- Payments