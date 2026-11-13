# Job Radar

**A multi-source job ingestion and intelligence platform built with Python and PostgreSQL.**

Job Radar is a personal software engineering project focused on collecting, normalizing, persisting, and eventually analyzing job opportunities from multiple recruitment platforms.

The project emphasizes reliable data ingestion, explicit domain boundaries, database integrity, and reproducible AI-assisted software development.

## Project Status

**Version:** 0.1.0  
**Stage:** Active development

The current implementation provides the domain model, PostgreSQL persistence and migrations, the Greenhouse collector and normalizer, and an ingestion service that orchestrates a crawl run: it reconciles configured boards, crawls each enabled company in its own transaction, isolates per-company failures and records crawl outcomes.

The `job-radar ingest` command runs one crawl from the command line (Step 4E). [`docs/PROJECT-STATE.md`](docs/PROJECT-STATE.md) has the current roadmap.

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
- **Collectors:** Source-specific data retrieval and normalization, currently focused on the Greenhouse public Job Board API.
- **Ingestion:** Source-agnostic orchestration of a crawl run: configuration reconciliation, per-company transactions, failure isolation and crawl outcome recording.
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

The commands below are for a bash or zsh shell on macOS or Linux.

- **Python 3.13** (ADR-0001). `make install` creates the virtual environment with `python3.13` from your `PATH`. If it is elsewhere, pass it explicitly: `make install PYTHON=/path/to/python3.13`. If it is missing, the Makefile suggests `uv python install 3.13`.
- **Docker with Docker Compose**, for the local PostgreSQL 17 instance in `docker-compose.yml`.
- **Make**.

### Installation

```bash
cp .env.example .env         # local settings; .env is gitignored
make install                 # creates .venv and runs: pip install -e ".[dev]"
source .venv/bin/activate    # optional; the Makefile calls .venv/bin directly
make up                      # starts PostgreSQL in Docker and waits until it is healthy
make migrate                 # applies the Alembic migrations to the configured database
```

`make install` installs the project in editable mode with its development tools. The editable install creates the `job-radar` console script (`.venv/bin/job-radar`, declared in `pyproject.toml`) and makes changes under `src/` take effect without reinstalling. After `source .venv/bin/activate`, `job-radar` is on your `PATH`; without activating, call `.venv/bin/job-radar`.

**Configuration.** Settings are read from `JOB_RADAR_*` environment variables, falling back to `.env`, then to built-in defaults that match `docker-compose.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `JOB_RADAR_DATABASE_URL` | the local Docker database `jobradar` | SQLAlchemy URL; must use the `postgresql+psycopg` driver |
| `JOB_RADAR_BOARDS_CONFIG_PATH` | `config/boards.yml` | Board configuration (ADR-0012) |
| `JOB_RADAR_HTTP_TIMEOUT_SECONDS` | `10.0` | Bounded HTTP timeout for Greenhouse requests (ADR-0009) |

The `POSTGRES_*` values in `.env` configure the Docker container; keep them consistent with `JOB_RADAR_DATABASE_URL`. The sample database credentials in `.env.example` are intended for local development only. Use separate, secure credentials for any deployed environment.

**PostgreSQL.** `make up` runs `docker compose up -d --wait`; `make down` stops the container and keeps its data volume. To use a PostgreSQL server you already run instead, point `JOB_RADAR_DATABASE_URL` at it and skip `make up`. Neither the CLI nor `make migrate` creates the database itself.

### Run the CLI

```bash
job-radar --version    # prints "job-radar 0.1.0"; so does job-radar with no arguments
job-radar ingest       # crawls every enabled board in the board configuration once
```

`ingest` makes **real HTTPS requests** to the public Greenhouse Board API (`boards-api.greenhouse.io`) for each board marked `enabled: true` in `config/boards.yml`. It **writes to the configured database**: it reconciles the configured boards into `companies`, which disables any company missing from the file, and records jobs and the crawl run's outcome. Edit `config/boards.yml` to choose which boards are crawled.

`ingest` takes no options and does not run migrations: run `make migrate` first. It prints a run summary on stdout and writes JSON Lines logs on stderr. It exits 0 whenever the run completes, including `partial_success` and `failed` runs. It exits 1 when settings, the board file or the database stop it. The contract is in [`docs/SPEC.md`](docs/SPEC.md), "Command-line interface".

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

### Verification Gates

The test suite includes unit, integration, database migration, and concurrency tests. PostgreSQL integration tests run against a real database instance.

All quality gates can be executed through a single command:

```bash
make check
```

This workflow combines static analysis, formatting validation, strict type checking, and automated tests to provide deterministic feedback during development with Claude Code.

Before a change is considered ready, the `verify-change` skill ([`.claude/skills/verify-change/`](.claude/skills/verify-change/)) runs each gate separately, asks before every integration run, reviews documentation impact, and reports what was and was not verified. Results come from those runs on a local machine; there is no continuous integration service yet, so this README does not quote test counts.

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
- [x] Connect collectors to the persistence layer through an executable ingestion pipeline
- [x] Implement CLI commands for manual collection and ingestion (`job-radar ingest`)
- [ ] Add Lever and Ashby job board integrations
- [ ] Implement configurable crawling schedules
- [ ] Add retry policies with exponential backoff and jitter
- [ ] Introduce rate limiting and concurrency controls
- [ ] Extend handling of partial failures and degraded API responses (partial: per-company failure isolation, `partial_success` runs and a board completeness check are implemented)
- [ ] Implement structured logging and crawl diagnostics (partial: structured log fields, persisted per-company crawl results and JSON Lines log output from the CLI are implemented)
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
