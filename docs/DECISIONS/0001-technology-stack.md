# ADR-0001: Technology stack

- Status: Accepted
- Date: 2026-09-15

## Context

The repository was empty. No language, framework or tooling had been chosen,
which blocked every other decision.

## Decision

- Python 3.13
- PostgreSQL
- SQLAlchemy 2 (typed ORM/Core)
- Alembic for migrations, from the first commit
- httpx for HTTP
- Pydantic v2 for source payload validation and the normalized model
- pytest
- Ruff for lint and format
- mypy for static typing
- Docker / Docker Compose for local PostgreSQL and reproducible test runs

FastAPI may be introduced later when an HTTP API is needed. It is not
required for the first vertical slice and is not a dependency yet.

## Consequences

- Schema changes go through Alembic from day one; no ad-hoc DDL.
- Pydantic v2 is used at both edges: validating untrusted source payloads and
  defining `NormalizedJob`. This satisfies the "never trust external data"
  principle at the boundary where data enters.
- The stack is synchronous for the MVP. `httpx` supports async if concurrent
  crawling is introduced later (ADR-0009).
