# Job Radar

**A multi-source job ingestion and intelligence platform built with Python and PostgreSQL.**

Job Radar is a personal software engineering project focused on collecting, normalizing, persisting, and eventually analyzing job opportunities from multiple recruitment platforms.

The project emphasizes reliable data ingestion, explicit domain boundaries, database integrity, and reproducible AI-assisted software development.

## Project Status

**Version:** 0.1.0  
**Stage:** Active development

The current implementation provides foundational domain, persistence, migration, and Greenhouse collection components.

The Greenhouse collector has been implemented, while the end-to-end ingestion CLI is still under development. The current CLI displays the installed application version.

## Technology Stack

- Python 3.13
- PostgreSQL
- SQLAlchemy 2
- Alembic
- Pydantic and Pydantic Settings
- HTTPX
- PyYAML
- pytest
- Ruff
- mypy (strict mode)
- Docker Compose

## Architecture

The application is organized around explicit engineering boundaries:

- **Domain:** Job normalization, ingestion contracts, crawl outcomes, and domain errors.
- **Collectors:** Source-specific data retrieval, currently focused on the Greenhouse public Job Board API.
- **Persistence:** SQLAlchemy repositories, PostgreSQL models, and transactional data operations.
- **Configuration:** Environment-based settings and version-controlled job board configuration.
- **Migrations:** Schema evolution through Alembic.

Additional architectural decisions are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the Architecture Decision Records under [`docs/DECISIONS/`](docs/DECISIONS/).

## Engineering Principles

- Idempotent database operations
- Explicit transaction ownership
- Source identity consistency
- Deterministic change detection
- Strong typing and validation
- Automated testing against real PostgreSQL
- Clear separation between domain logic and external integrations

## Getting Started

### Requirements

- Python 3.13
- Docker with Docker Compose
- Make

### Installation

```bash
cp .env.example .env
make install
make up
make migrate
```

The sample database credentials in `.env.example` are intended for local development only. Use separate, secure credentials for any deployed environment.

### Run the Current CLI

```bash
.venv/bin/job-radar
```

The current command displays the application version. End-to-end ingestion commands are planned.

### Quality Checks

```bash
make lint
make typecheck
make test-unit
make test-integration
make check
```

Integration tests require a running PostgreSQL instance. `make check` executes linting, strict type checking, and the complete test suite.

## AI-Assisted Engineering

Job Radar is being developed with Claude Code as an AI coding assistant.

The development workflow emphasizes human engineering judgment, explicit requirements, architectural constraints, and automated verification.

The repository includes:

- [`CLAUDE.md`](CLAUDE.md) for project-level agent guidance
- [`docs/SPEC.md`](docs/SPEC.md) for project specifications
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for architectural context
- Architecture Decision Records for important technical choices
- Automated tests, linting, and strict type checking to verify changes

The goal is to use AI assistance while maintaining code quality, architectural consistency, and reproducible development practices.

## Automated Verification & Code Quality

Job Radar uses a reproducible verification workflow to maintain code quality and architectural consistency throughout AI-assisted development.

### Current Verification Results

- **353 automated tests passing**, including unit, integration, database migration, and concurrency tests.
- **51 source files** successfully checked with mypy in strict mode.
- **79 files** validated by Ruff's formatter.
- **Ruff linting:** all checks passed.
- **PostgreSQL integration tests:** executed against a real database instance.

All quality gates can be executed through a single command:

```bash
make check
```

This workflow combines static analysis, formatting validation, strict type checking, and automated tests to provide deterministic feedback during development with Claude Code.

These results reflect the latest locally executed verification run, not a continuous integration service.

## Roadmap

Job Radar is being developed incrementally, with an emphasis on reliable data ingestion, maintainable architecture, automated verification, and AI-assisted engineering.

The roadmap represents planned development directions rather than committed delivery dates.

### Phase 1 — Core Infrastructure & Data Integrity

- [x] Establish a modular Python project architecture
- [x] Define domain models and ingestion contracts
- [x] Implement PostgreSQL persistence with SQLAlchemy
- [x] Establish database migrations with Alembic
- [x] Implement idempotent job upserts and change detection
- [x] Enforce source consistency and database integrity constraints
- [x] Implement transaction ownership boundaries
- [x] Introduce automated unit and integration testing
- [x] Configure strict static typing with mypy
- [x] Establish linting and formatting with Ruff
- [x] Document architecture and engineering decisions

### Phase 2 — Collection & Ingestion Pipeline

- [x] Implement the Greenhouse job board collector
- [x] Introduce configurable job board definitions
- [ ] Connect collectors to the persistence layer through an executable ingestion pipeline
- [ ] Implement CLI commands for manual collection and ingestion
- [ ] Add Lever and Ashby job board integrations
- [ ] Implement configurable crawling schedules
- [ ] Add retry policies with exponential backoff and jitter
- [ ] Introduce rate limiting and concurrency controls
- [ ] Extend handling of partial failures and degraded API responses
- [ ] Implement structured logging and crawl diagnostics
- [ ] Add crawl execution metrics and operational reporting
- [ ] Support incremental collection and checkpoint recovery

### Phase 3 — Job History & Data Quality

- [ ] Track historical changes in job descriptions and metadata
- [ ] Detect newly published, updated, and removed job postings
- [ ] Implement configurable policies for inactive job detection
- [ ] Introduce cross-source duplicate detection
- [ ] Normalize job locations, employment types, and compensation ranges
- [ ] Add company identity resolution across different sources
- [ ] Track data freshness and source reliability
- [ ] Build automated data quality reports
- [ ] Introduce audit trails for ingestion and normalization decisions

### Phase 4 — Search & Job Intelligence

- [ ] Implement full-text job search
- [ ] Add advanced filtering by technology, location, seniority, salary, and remote eligibility
- [ ] Extract structured skills and requirements from job descriptions
- [ ] Introduce configurable job matching based on candidate preferences
- [ ] Implement explainable relevance scoring
- [ ] Explore semantic search using embeddings
- [ ] Identify similar job opportunities across companies
- [ ] Track recurring job requirements and technology trends
- [ ] Build personalized job recommendations

### Phase 5 — API & User Interface

- [ ] Develop a versioned REST API
- [ ] Implement a responsive dashboard
- [ ] Build searchable and sortable job listings
- [ ] Add saved searches and customizable filters
- [ ] Provide detailed job views and historical change timelines
- [ ] Implement bookmarking and application tracking
- [ ] Add personalized dashboards and job discovery workflows
- [ ] Visualize job market trends and technology demand
- [ ] Support data export in CSV and JSON formats

### Phase 6 — Automation & Notifications

- [ ] Implement scheduled job discovery workflows
- [ ] Add notifications for newly matching opportunities
- [ ] Support email-based job alerts
- [ ] Introduce configurable notification rules and thresholds
- [ ] Detect significant changes to saved job postings
- [ ] Add recurring personalized job digests
- [ ] Track application deadlines where available

### Phase 7 — Production Readiness & Scalability

- [ ] Add CI pipelines for linting, type checking, and automated testing
- [ ] Implement containerized application deployment
- [ ] Introduce background workers for asynchronous ingestion
- [ ] Add distributed task scheduling where justified
- [ ] Implement monitoring, health checks, and operational alerts
- [ ] Introduce performance benchmarking and database query optimization
- [ ] Establish backup and recovery procedures
- [ ] Implement secure configuration and secrets management
- [ ] Add deployment documentation and operational runbooks

### Phase 8 — Advanced AI-Assisted Capabilities

- [ ] Explore LLM-assisted job description classification
- [ ] Implement structured extraction of skills and responsibilities
- [ ] Develop explainable AI-assisted job matching
- [ ] Generate personalized summaries of relevant opportunities
- [ ] Identify gaps between candidate profiles and job requirements
- [ ] Explore natural-language search over indexed opportunities
- [ ] Evaluate AI-assisted market trend analysis
- [ ] Benchmark AI-generated classifications against deterministic baselines

### Long-Term Vision

Evolve Job Radar into a reliable, extensible job intelligence platform that combines structured data engineering, automated job discovery, historical analytics, and personalized recommendations.

The project will continue to prioritize reproducibility, explicit engineering decisions, strong automated verification, and human oversight of AI-assisted development.

## Ownership

Job Radar is an independently developed personal project. Development is ongoing.
