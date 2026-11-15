# Development Log

Milestone history, newest first. Add each milestone's entry at the top, in
the milestone's own commit. That commit cannot name its own SHA, so the entry
says how to find it. Do not rewrite old entries except to correct a factual
error, marked as such.

This log records what was done, where, and what evidence exists. It does not
restate design: behavior is in `SPEC.md`, boundaries in `ARCHITECTURE.md`,
decisions in `DECISIONS/`. For the current status see
[`PROJECT-STATE.md`](PROJECT-STATE.md).

**Evidence labels**

- **Captured**: produced by a check run with a captured exit code and output
  (verify-change, H-004 onwards). Says where the output lived.
- **Reported**: stated by the maintainer or in a commit message; no output is
  preserved in the repository.
- **Inferred**: concluded from Git history or documents; not recorded
  directly. The reasoning is given.
- **Not recorded**: no evidence exists in the repository.

Dates are commit dates. Step IDs (0–4E) are product milestones; H-IDs are
harness milestones.

---

## H1: Agent Observability & Efficiency

- **Status:** complete, 2026-11-13. Started 2026-11-13 on explicit approval.
  (Recorded before the commit as "awaiting the maintainer's review and
  commit"; corrected after the maintainer reviewed and the commit landed.)
- **Objective:** measure token usage, time, activity, estimated cost and
  verification quality of Claude Code sessions per milestone, locally and
  read-only, without coupling to Job Radar (`tools/harness_metrics/`,
  `docs/metrics/README.md`).
- **Discovery (read-only):** transcripts write an assistant message once per
  content block, repeating its usage (593 lines, 263 messages at the time),
  so totals must be deduplicated by message id; `usage.iterations` repeats
  the top level; thinking is a subset of output; cache writes are split by
  lifetime; no cost is recorded; no subagent records exist locally. The
  preliminary "Step 4G" figures (62,523,086 tokens, 11 m 58 s) were exact
  but covered the session from 4E through the 4G implementation turn; 11 m
  58 s was that turn's active time.
- **Decisions:** D1 `tools/harness_metrics`, standard library only, no
  import either way with `job_radar` (tested); D2 committed snapshots of
  aggregates only; D3 hand-maintained segments, suggested from commit times;
  D4 versioned pricing with source and date, unknown when unpriced; D5 one
  call per `(session, message.id)`, conflicting duplicates make usage
  unknown; D6 a hand-maintained outcomes ledger; D7 built-in tool names only
  (others count as `other`); D8 amended: session ids and paths only in the
  Git-ignored local file; D9 snapshot milestones when they close.
- **Phases (each verified before the next):**
  1. H1-1 parser (`transcript.py`), aggregation (`aggregate.py`), privacy
     canaries; the parser reproduced the discovery figures exactly.
  2. H1-2 pricing (`pricing.py`, `docs/metrics/pricing.toml`): the committed
     table has no rates, so estimates are unknown until the maintainer enters
     them from the official page.
  3. H1-3 local configuration (`config.py`), boundary suggestions
     (`boundaries.py`, read-only `git log`), milestone reports and snapshots
     (`report.py`), CLI (`cli.py`).
  4. H1-4 outcomes ledger (`outcomes.py`, `docs/metrics/outcomes.jsonl`),
     backfilled for 4E–4G from this log: first-pass yield counts decisive
     runs only; escaped defects are unknown until checked.
  5. H1-5 documentation, `make metrics`, TOOL-RADAR entry.
- **Reconciliation:** the committed snapshots for 4E (turns 2–7), 4F (8–13)
  and 4G (14–19) match an independent computation from the raw transcript in
  calls, every token category and active time. Turn 1 (the fresh-session
  check) is mapped locally as `H-005-followup`. All boundaries are labelled
  inferred; the end turns are the turns of the milestones' commits.
- **Tests:** `tests/unit/harness/` (parsing, deduplication, conflicts,
  incomplete usage, drift, turns, tools, pricing, configuration, boundaries,
  outcomes, reports, CLI privacy, import boundary). Synthetic transcripts
  only (`tests/support/claude_transcripts.py`). Mutation check: disabling
  the tool allowlist fails the privacy tests.
- **Final review (2026-11-13), narrow corrections:**
  - Boundaries: each end turn (7, 13, 19) contains its milestone's commit,
    and each start turn (2, 8, 14) is the first prompt naming the
    milestone, after the previous commit; turns 3–6, 9–12 and 15–18 name
    only their milestone (a local keyword check printing labels, not text).
    Kept `inferred`: `reviewed` means a person has checked them.
  - The H1 segment no longer ends at a placeholder turn 99; it ends at the
    review turn, and a segment beyond the recorded turns is now reported
    (`segment_extends_beyond_recorded_turns`) instead of absorbing later
    turns.
  - "Peak context" is renamed `largest_request_input`: the largest request's
    input as the API's usage fields account it, which is accounting data,
    not a measurement of the context window. Values re-checked from the raw
    transcript.
  - Pricing rates must state `inference_geo` and an explicit request-size
    range (`"none"` for no upper limit), so a long-request price tier cannot
    be overlooked silently; the steps to enable estimates are documented.
  - Quality labels: `reruns` became `later runs (any reason)`, plus
    `failures re-run` (each failure once) and the data source; first-pass
    yield is shown as a fraction of decisive first runs.
- **Verification (captured, H1 session; output in a session scratchpad, not
  preserved):** after the final-review corrections: Ruff lint, Ruff format
  (`130 files already formatted`), mypy (`95 source files`): PASS. Unit:
  `make test-unit`, exit 0, `871 passed, 267 deselected` (142 of them H1).
  `git diff --check`: exit 0. `validate`: ok. Privacy scan of every changed
  and untracked file and of every CLI command's output: no real session id,
  home path, account identifier or key; `.harness/` is Git-ignored.
  Integration: not applicable (H1 touches no database code) and not run.
  DOC-IMPACT: PASS. Only this bullet changed after the gate run.
- **Closure (2026-11-13):** the maintainer reviewed and approved the 4E
  (turns 2–7), 4F (8–13) and 4G (14–19) boundaries, now `reviewed` in the
  local configuration. The three snapshots were regenerated; compared field
  by field with the committed ones, the only changes are `boundary.status`
  (`inferred` to `reviewed`) and the removal of the
  `boundary_inferred_not_reviewed` gap. Pricing is unchanged: no rates, so
  costs stay unknown. ccusage was assessed and not adopted.
- **Commit:** `f3f58caf663e9d105f27bac1e6fbac8cb6063f20`
  (`feat(harness): add local Claude Code observability and milestone
  metrics`). The snapshot regeneration and this closure were committed
  with the approved 4H contract
  (`git log --format=%h -F --grep='docs(harness): finalize 4H contract and H2 evaluation protocol'`).
- **Follow-up:** pricing rates (the maintainer, from the official page,
  including whether long requests are priced differently); an
  escaped-defect check per milestone.

## 4G: Deactivation on verified absence

- **Status:** complete, 2026-11-12. Started 2026-11-12 on explicit
  approval.
- **Objective:** deactivate a company's active jobs absent from a
  successful, completeness-verified crawl, preserving history (ADR-0017;
  SPEC, "Active state"). Out of scope: confirmation or override mechanisms,
  persisted skip reasons, `--include-inactive`, retries, new sources.
- **Contract (approved):** D1 new ADR-0017, ADR-0004 stays Accepted; D2
  `jobs.deactivated_at`; D3 `completeness_verified` and `jobs_deactivated`
  persisted; D4 `Collector.fetch_jobs` returns `FetchedBoard`; D5 an empty
  board never deactivates; D6′ skip when `2·K > P` and `K ≥ 5`, logged as a
  WARNING, not a failure, nothing remembered between runs; D7 `jobs`
  unchanged; D8′ an invalid posting protects its job only by a canonical
  id, else the company skips; D9 absence by id set, not by clock; E1 skip
  reasons in logs only; E2 P counted before upserts in the company
  transaction.
- **Implementation checkpoints (each verified before the next):**
  1. ADR-0017 and its index entry.
  2. `FetchedBoard` in `job_radar.collectors.base`; the Greenhouse
     collector reports completeness; fakes adapted. Unit tests: 663 passed.
  3. Migration `0005_job_deactivation`, models, upsert clears
     `deactivated_at`. Offline `alembic upgrade/downgrade --sql` rendered
     the expected DDL without a database.
  4. `JobRepository.count_active`, `count_absent`, `deactivate_absent`
     (refuses an empty id set); crawl records carry the new fields; ports,
     adapter and fakes.
  5. `job_radar.ingestion.deactivation`: pure guards, the D6′ boundaries and
     the canonical-id check. 32 unit tests.
  6. Service wiring in the success transaction; logs; counters. One
     existing test's expected call order now starts with `count_active`
     (E2). Mutation check: disabling the large-disappearance rule, the
     prechecks or the canonical-id check each fails the new service tests.
  7. CLI summary ` deactivated=N`, four log fields; a Greenhouse test pins
     that an invalid posting's id equals the id a valid posting stores (the
     schema rejects non-integer ids, so a reported id is always canonical).
  8. Integration and acceptance tests written; docs.
- **Tests:** `tests/unit/ingestion/test_deactivation_policy.py`,
  `tests/unit/ingestion/test_ingestion_deactivation.py`, collector and
  normalizer additions, CLI summary and log-field tests.
  `tests/integration/test_ingestion_pipeline_deactivation.py` (14 tests:
  deactivation and history, reactivation, idempotent rerun, failed crawl,
  each guard, D6′ boundaries, a trigger failing the deactivating `UPDATE`
  rolls back the whole company); migration 0005 up and down; schema
  nullability and non-negative checks; `assert_run_is_consistent` now also
  checks the deactivated sum and completeness; three acceptance tests in
  `tests/integration/test_cli_acceptance.py`.
- **Verification (captured, 4G session; output in a session scratchpad,
  not preserved):** Ruff lint, Ruff format (`112 files already
  formatted`), mypy (`78 source files`): PASS. Unit: `make test-unit`,
  exit 0, `729 passed, 267 deselected`. `git diff --check`: exit 0.
  DOC-IMPACT: PASS (stale "deferred deactivation" docstrings in
  `crawl_repository.py` and `models.py` corrected before the final run).
  Integration: not run then, PostgreSQL access not yet authorized; 267
  integration tests collect. Overall: PARTIAL.
- **Integration (captured, 2026-11-12, on the maintainer's single-use
  authorization, now consumed):** preflight per verify-change section 4:
  target `postgresql+psycopg` on `localhost:5432` from the built-in default
  (no `.env`, no `JOB_RADAR_*` or `PG*` variables, no pg service or pass
  file), configured database `jobradar` (read only, by
  `test_database_connection.py`); the section 4b grep matched the skill's
  description; connectivity probe `True`. A read-only inspection, on
  read-only connections, found `jobradar_schema_test` and
  `jobradar_migration_test` holding only the project's tables, every data
  table empty: nothing to preserve. `make test-integration`: exit 0,
  `267 passed, 729 deselected in 9.06s`, no failures, errors, skips or
  warnings. Verified against PostgreSQL: migration 0005 upgrade and
  downgrade and the drift check; deactivation with history preserved;
  reactivation; idempotent rerun; a failed crawl changing no job; each
  guard (unverified completeness, empty board, invalid posting with and
  without an id, all invalid, disabled company); the D6′ boundaries
  (10→4 skipped with a WARNING, exactly half deactivated, 4 of 4
  deactivated); a trigger failing the deactivating `UPDATE` rolling back the
  company's upserts byte for byte; three deactivation acceptance tests
  through the CLI; and every pre-existing test, with
  `assert_run_is_consistent` now checking the deactivated sum and
  completeness. `git status` was identical before and after.
- **Final non-database run (captured, after the closing documentation):**
  Ruff lint, Ruff format (`112 files already formatted`), mypy
  (`78 source files`): PASS. Unit: `make test-unit`, exit 0,
  `729 passed, 267 deselected`. `git diff --check`: exit 0. DOC-IMPACT:
  PASS. No application code or tests changed after the integration run.
  Only this bullet changed after this run.
- **Commit:** `git log --format=%h -F --grep='feat(ingestion): deactivate jobs absent from verified crawls'`.
- **Follow-up:** none required. Known limits are in PROJECT-STATE, "Known
  issues".

## 4F: Job listing query

- **Status:** complete, 2026-11-12. Started 2026-11-12 on explicit
  approval.
- **Objective:** a read-only `job-radar jobs` command over the stored jobs
  (SPEC, "Job listing and query"). Out of scope: ingestion changes, HTTP,
  migrations, new dependencies, deactivation, classification.
- **Phase 1, contract (read-only):** decisions D1–D15 approved, D4 and D15
  amended by the maintainer: `--new-in-last-run` is documented as a
  timestamp correlation (`first_seen_at = started_at`), not a recorded link;
  `jobs` must not fail on ingestion-only settings. A1 (`jobs_new` beside the
  listing), A2 (later unfinished runs reported) and four log fields
  (`later_unfinished_runs`, `started_at`, `limit`, `more_available`)
  approved.
- **Phase 2, implementation:**
  - `src/job_radar/settings.py`: `DatabaseSettings` (database URL only);
    `Settings` extends it with the board file and HTTP timeout, unchanged
    for `ingest`.
  - `src/job_radar/persistence/job_query.py`: `JobQuery` (latest finished
    run by highest id, later unfinished runs, the listing), `JobCriteria`,
    `ListedJob`, `JobPage`. SQLAlchemy Core, `ILIKE` with autoescape, order
    `first_seen_at DESC, id DESC`, limit + 1. Statements only.
  - `src/job_radar/persistence/session.py`: `create_read_only_engine` sets
    `REPEATABLE READ` and read-only on every new DBAPI connection;
    `read_only_session` always rolls back. Contract refinement said
    connection-level execution options. Reading SQLAlchemy 2.0.54 showed
    that `pool_pre_ping` runs `SELECT 1` on a reused connection, which in
    psycopg's non-autocommit mode opens a transaction, and psycopg refuses to
    change these characteristics inside one. They are therefore set when the
    connection is opened. Same contract, different mechanism; inferred from
    the code, not observed, and covered by an integration test on a reused
    pooled connection.
  - `src/job_radar/cli.py`: `jobs` subcommand (mutually exclusive time
    selectors, validated `--since` and `--limit`, non-empty filters, repeated
    options rejected), `run_jobs`, `render_jobs_table` with
    `display_text` sanitization (Cc, Cf, Cs, Zl, Zp), `render_jobs_jsonl`
    (ASCII-only), context records on stderr, four new log fields. Database
    errors are reported through `to_persistence_error` (class and SQLSTATE).
- **Tests:** `tests/unit/test_cli_jobs.py` (parsing, selection, run context,
  table and JSONL exact output, sanitization including a check that the
  tests do not rely on the module's own category list, settings isolation,
  safe errors, engine disposal, rollback without commit, no HTTP client or
  board file). `tests/unit/test_job_query_construction.py` (criteria
  validation, compiled SQL, the no-commit scan of `job_query`, the
  read-only connection hook, `DatabaseSettings` and `Settings`).
  `tests/integration/test_job_query.py` (24 tests: run selection, timestamp
  correlation, inclusive `--since`, order and limit, `is_active`, filters,
  wildcards, NULLs, accents, company by name or token, stored values; the
  read-only engine's isolation level, read-only flag on a reused pooled
  connection, and SQLSTATE 25006 for writes). Three `jobs` acceptance tests
  in `tests/integration/test_cli_acceptance.py`.
- **Verification (captured, 4F session; output in a session scratchpad,
  not preserved):** Ruff lint, Ruff format (`106 files already
  formatted`), mypy (`73 source files`): PASS. Unit: `make test-unit`,
  exit 0, `657 passed, 242 deselected`. `git diff --check`: exit 0.
  DOC-IMPACT: PASS. Integration: not run then, PostgreSQL access not yet
  authorized; 242 integration tests collect. Overall: PARTIAL.
- **Integration (captured, 2026-11-12, on the maintainer's single-use
  authorization, now consumed):** preflight per verify-change section 4:
  target `postgresql+psycopg` on `localhost:5432` from the built-in default,
  configured database `jobradar` (read only, by
  `test_database_connection.py`); destructive operations only in
  `jobradar_schema_test` and `jobradar_migration_test`; the section 4b grep
  matched the skill's description; connectivity probe `True`. `make
  test-integration`: exit 0, `242 passed, 657 deselected in 7.28s`, no
  failures, skips or warnings. That includes the 24 tests in
  `tests/integration/test_job_query.py` and all 7 CLI acceptance tests (4
  from 4E, 3 for `jobs`). The read-only, repeatable-read engine was verified
  against PostgreSQL: `SHOW` reports `repeatable read` and read-only `on`,
  also on a reused, pre-pinged pooled connection, and `INSERT`, `UPDATE` and
  `DELETE` are refused with SQLSTATE 25006. This confirms the mechanism
  change above, which had been inferred from the code. `git status` was
  identical before and after; the databases were not inspected afterwards.
- **Closing documentation:** this entry, PROJECT-STATE (4F complete, no next
  milestone approved, `jobs` limitations and the noted grep gap under known
  issues) and the README status line. No application code or tests changed
  after the integration run.
- **Commit:** `git log --format=%h -F --grep='feat(cli): add the read-only job-radar jobs command'`.
- **Follow-up:** none required. Known limits are in PROJECT-STATE, "Known
  issues".

## 4E: Ingestion CLI

- **Status:** complete, 2026-11-12. Started 2026-11-11 on explicit
  approval.
- **Objective:** make the ingestion service executable as `job-radar ingest`,
  following SPEC, ARCHITECTURE "Transaction ownership", ADR-0008, ADR-0010 and
  ADR-0012. Out of scope: refactors, new sources, retries, scheduling,
  concurrency, non-zero exit for partial failure, new dependencies.
- **Phase 1, contract design (read-only):** found that nothing configured
  logging, so in a real process the service's INFO completion record and all
  `extra` fields were lost (SPEC "Structured logging" was unmet). Proposed
  decisions D1–D8; the maintainer approved them with amendments: keep
  `job-radar` with no arguments printing the version (D2), and put the test
  seams on an internal function, not on `main` (D7).
- **Phase 2, implementation:**
  - `src/job_radar/cli.py`: argparse parser (`ingest`, `--version`);
    `run_ingest(stdout, stderr, transport=None, clock=None)` as the
    composition root; `render_summary`; `JsonLinesFormatter` with an
    allowlist of fields; `structured_logging` context manager, attached to
    the `job_radar` logger only and restored on exit. Settings and the board
    file load before any engine or client exists. Exits 1 on settings,
    board-file and `PersistenceError` failures. Other exceptions propagate.
  - `create_client` takes an optional `transport` (default `None`, httpx's
    network transport), so tests stub HTTP and keep production timeout and
    headers.
  - SPEC "Command-line interface" (approved 2026-11-11) and an amended
    exit-code paragraph in "Run reporting". ARCHITECTURE, README and the
    `http_timeout_seconds` description updated.
  - Found by the first verify-change DOC-IMPACT review, then fixed:
    ADR-0008 gains a 2026-11-11 addendum ("only an unhandled crash exits
    non-zero" now means a run that does not complete); PROJECT-STATE's 4E
    scope repeated the old wording. verify-change section 4b now greps and
    describes the CLI acceptance tests, which commit to
    `jobradar_schema_test`, and its report template no longer says no
    acceptance tests exist.
- **Tests:** `tests/unit/test_cli.py` (39 tests: parsing, `main(["ingest"])`
  through the real `run_ingest` on the process streams, exit codes for each
  run status, wiring, summary, settings, board-file, database-URL and
  persistence failures, crash propagation, engine and client release, log
  allowlist, traceback suppression, handler restore). One new collector unit
  test for the injected transport. `test_cli_entry_point_succeeds` now calls
  `main([])`: under pytest, `main()` would parse pytest's own argv.
  `tests/integration/test_cli_acceptance.py` (4 tests, SPEC "MVP Acceptance
  Criteria" plus the all-boards-failing exit code). They use the durability
  tests' isolation on `jobradar_schema_test`.
- **Pre-integration review (2026-11-12):** the ADR-0008 addendum and the
  verify-change edits were reviewed and accepted as written. Found that
  `main(["ingest"])` was tested only with `run_ingest` stubbed; added the two
  entry-point unit tests above.
- **Closing documentation (2026-11-12):** README setup instructions (Python
  3.13, virtual environment, `make install` as an editable install exposing
  `job-radar`, configuration, PostgreSQL, migrations, CLI usage, and that
  `ingest` makes real requests to enabled Greenhouse boards and writes to the
  configured database); the stale timeout comment in `.env.example`; this
  entry and PROJECT-STATE.
- **Verification (captured, 4E session; output in a session scratchpad,
  not preserved):** first run FAIL on DOC-IMPACT only (the four findings
  above). Second run, after those fixes: Ruff lint, Ruff format, mypy
  (`69 source files`): PASS. Unit: `make test-unit`, exit 0, `576 passed,
  215 deselected`. DOC-IMPACT: PASS. Integration: BLOCKED, because the
  maintainer has not yet authorized PostgreSQL access. The acceptance tests
  have never run. Overall: PARTIAL. Third run, after the pre-integration
  review added two entry-point unit tests: lint, format, mypy (`69 source
  files`) PASS; unit exit 0, `578 passed, 215 deselected`; integration not
  run.
- **Integration (captured, 2026-11-12, on the maintainer's single-use
  authorization, now consumed):** preflight per verify-change section 4:
  target `postgresql+psycopg` on `localhost:5432` from the built-in default
  (no `.env`, variable unset), configured database `jobradar`; test
  databases `jobradar_schema_test` and `jobradar_migration_test`, the only
  ones `CREATE DATABASE` and `DROP SCHEMA public CASCADE` touch; `jobradar`
  itself only receives `SELECT`s; connectivity probe `True`. `make
  test-integration`: exit 0, `215 passed, 578 deselected in 7.85s`, no
  failures, skips or warnings. All four acceptance tests in
  `tests/integration/test_cli_acceptance.py` passed. `git status` was
  identical before and after; the databases were not inspected afterwards,
  which would have exceeded the authorization.
- **Final non-database run (captured, after the closing documentation):**
  Ruff lint, Ruff format, mypy (`69 source files`): PASS. Unit: `make
  test-unit`, exit 0, `578 passed, 215 deselected`. DOC-IMPACT: PASS.
  `git diff --check`: exit 0. Integration was not rerun: after it, only
  README, PROJECT-STATE, this entry and a comment in `.env.example` (which
  no code reads) changed, so its evidence still covers the code and tests.
  Only this bullet changed after the run.
- **Commit:** `git log --format=%h -F --grep='feat(cli): add the job-radar ingest command'`.
- **Follow-up:** none required. Known limits are in PROJECT-STATE, "Known
  issues".

## H-005: Context Retrieval Efficiency

- **Status:** complete, 2026-11-11.
- **Objective:** measure and improve how an agent recovers context:
  retrieval cost, freshness, handoff accuracy. Out of scope: application
  features, retrieval infrastructure (vector search, RAG, MCP, indexes),
  hooks and scripts.
- **Phase 1, audit (at `423751b`):**
  - Observed: `CLAUDE.md` and Git status injected at session start went
    stale during a long session; skill listings were refreshed.
  - Observed: five representative questions each needed three or fewer
    search or read operations. No retrieval failure, so no infrastructure is
    justified.
  - Observed: volatile claims had drifted (TOOL-RADAR "0 through 4D-4",
    CONCEPTS "284 tests" and "ADRs (15)"). 4D-5's DOC-IMPACT missed the
    first: its review map does not lead from domain code to TOOL-RADAR.
  - Observed: no planned milestone had a recorded scope.
- **Phase 2 and final refinements:**
  - Volatile counts replaced with references or dated "at Step 3"
    (TOOL-RADAR, CONCEPTS, PROJECT-BOOTSTRAP).
  - PROJECT-STATE: objective and out-of-scope for each open milestone. 4E's
    is derived from SPEC, ARCHITECTURE, ADR-0008, ADR-0010 and ADR-0012; the
    command's name and arguments are left undecided.
  - PROJECT-STATE's safety section is a summary pointing to `CLAUDE.md` and
    verify-change as authoritative, with no requirement removed.
  - Freshness rules in `CLAUDE.md` and project-handoff: current HEAD, status
    and files from disk; session-start snapshots are not authoritative;
    staleness checked with `git log <snapshot>..HEAD`; unresolved
    disagreements reported, not settled.
  - verify-change DOC-IMPACT: on a milestone change, search for volatile
    claims (milestone IDs, planned or pending status, current counts) and
    read only the matching sections.
- **Measurements (bytes of command output, not tokens; the operation counts
  are the minimum paths, taken by an agent that already knew the code):**
  - Questions 1–4 unchanged: 434 B, ~9.1 KB, 154 B, ~8.0 KB.
  - Question 5 (next milestone and its scope): 818 B and partial before;
    2,304 B and complete after.
  - Handoff collection: 15,145 B before; 16,796 B after.
  - `CLAUDE.md`, loaded every session: 3,097 B before; 3,426 B after.
- **Decisions:** no code map, hooks, scripts or indexes. Splitting
  verify-change's database section (5,067 of 23,303 B, 22%) into an
  on-demand file was deferred: the saving is about 5 KB on runs without
  integration, no context pressure was observed, and a split would move
  safety steps out of the main file.
- **Verification (captured, H-005 session; output in a session scratchpad,
  not preserved):** Ruff lint, Ruff format, mypy: PASS. Unit: `make
  test-unit`, exit 0, `538 passed, 211 deselected`. Integration: SKIPPED
  (docs/harness scope). DOC-IMPACT: PASS. Not validated in a fresh session.
- **Commit:** `git log --format=%h -F --grep='feat(harness): improve context freshness and retrieval'`.
- **Follow-up:** a fresh-session check of the freshness rules (`/context`,
  `/project-handoff`), run by the maintainer.

## 4D-5: Lone-surrogate Unicode validation

- **Status:** complete, 2026-11-11.
- **Objective:** a posting with an unpaired UTF-16 surrogate becomes one
  `invalid` posting, as a posting with NUL already does, instead of an error
  outside the domain contract.
- **Root cause:** a lone `\uD800`–`\uDFFF` JSON escape decodes to a surrogate
  code point, which has no UTF-8 encoding. psycopg's text dumper raises
  `UnicodeEncodeError` (verified without a database). That is not a
  `psycopg.Error`, so SQLAlchemy does not wrap it, the store adapter does not
  translate it to `PersistenceError`, and the service propagates it as a
  programming error (inferred from source; the pre-fix behavior was not
  executed against PostgreSQL). Before 4D-5 (`90097a2`) the four required
  text fields were already rejected, but only as pydantic-core's
  `string_unicode`, a side effect of `strip_whitespace`. Optional text fields
  and `raw_data` were not checked.
- **Implementation:** `src/job_radar/domain/job.py` extends the NUL check to
  surrogates, with category `surrogate_character`, in every text field and in
  any key or value of `raw_data`. Required text is checked before pydantic's
  string constraints (the validator follows `StringConstraints`, or the
  constraints are dropped), so it reports `surrogate_character` rather than
  `string_unicode`. NUL takes precedence when both occur. SPEC, "Text
  PostgreSQL cannot store", records the contract (approved 2026-11-11).
- **Tests:** 55 unit tests (`tests/unit/domain/test_normalized_job.py`,
  `tests/unit/collectors/test_greenhouse_normalizer.py`) and 2 pipeline
  integration tests (`tests/integration/test_ingestion_pipeline.py`): a
  surrogate in an optional text field, and one only in nested `raw_data`, each
  followed by a valid posting. The integration tests serve ASCII JSON
  containing the `\ud800` escape, as the API would; httpx's `json=` encodes as
  UTF-8 and cannot carry an unpaired surrogate. Run against `90097a2`'s
  `src/`, 50 of the new unit tests failed, so they detect the defect.
- **Decisions:** reject, never replace (as for NUL). Any surrogate left in a
  decoded `str` is unpaired, because a valid pair decodes to one non-BMP code
  point.
- **Verification (captured, 4D-5 session; output in a session scratchpad,
  not preserved):**
  - Ruff lint, Ruff format, mypy: PASS.
  - Unit: `make test-unit`, exit 0, `538 passed, 211 deselected`.
  - Integration: `make test-integration`, exit 0, `211 passed, 538
    deselected`, run once on explicit authorization after the verify-change
    safety checks (local `postgresql+psycopg`, test databases
    `jobradar_schema_test` and `jobradar_migration_test`).
  - DOC-IMPACT: PASS. Overall: PASS.
  - Only documentation changed after these runs; code and tests are
    identical to the commit.
- **Commit:** `git log --format=%h -F --grep='fix(domain): reject lone Unicode surrogates'`.
- **Follow-up:** none required. H-005 is next.

## H-004b: Project Traceability & Context Handoff

- **Status:** complete, 2026-11-11.
- **Objective:** let any agent reconstruct status, history, evidence and next
  steps without conversation memory.
- **Implementation:** `docs/PROJECT-STATE.md` (snapshot), this log (backfilled
  from Git and repository documents, with evidence labels), a "Recovering
  context" section in `CLAUDE.md`, and the read-only `project-handoff` skill.
  verify-change's DOC-IMPACT now checks that a completed milestone updates
  both documents. Stale statements corrected: `CONCEPTS.md` (no commits),
  TOOL-RADAR (DOC-IMPACT not exercised), `ARCHITECTURE.md` (CLI step),
  `README.md` (CLI as next step).
- **Decisions:** progressive disclosure: PROJECT-STATE is the entry point;
  log entries are read one at a time; SPEC, ARCHITECTURE and the ADRs stay
  authoritative. No hook, script or retrieval system.
- **Verification (captured, H-004b session; output in a session scratchpad,
  not preserved):** Ruff lint, Ruff format, mypy: PASS. Unit: `483 passed,
  209 deselected`. Integration: SKIPPED (docs/harness scope). DOC-IMPACT:
  PASS. A handoff rehearsal on the uncommitted tree exposed a gap: the skill
  could not handle an uncommitted PROJECT-STATE. It was fixed before commit.
- **Commit:** the commit that added this file
  (`git log --diff-filter=A --format=%h -- docs/DEVELOPMENT-LOG.md`).
- **Follow-up:** 4D-5 is next. Nothing enforces keeping these documents
  current.

## H-004: Verification Evidence Integrity

- **Status:** complete, 2026-11-11.
- **Objective:** no executed check may be reported PASS without reliable
  evidence.
- **Implementation:** verify-change gains the UNVERIFIED check status, a
  foreground capture pattern (`check_exit=$?` right after the gate, output
  saved outside the repository), per-tool PASS evidence, a list of patterns
  that mask evidence, and the rule that an integration run consumes its
  approval even when its result is lost. An UNVERIFIED check, required or
  not, rules out overall PASS.
- **Files:** `.claude/skills/verify-change/SKILL.md`, `report-template.md`,
  `CLAUDE.md` (Verification), `docs/ai-engineering/TOOL-RADAR.md`.
- **Decisions:** a procedural rule, not an execution framework; no script was
  added. Motivated by a Step 4D-4 incident (TOOL-RADAR, "Symptom that
  justified evidence capture").
- **Verification (captured, H-004 session; output in a session scratchpad,
  not preserved):** Ruff lint, Ruff format, mypy: PASS. Unit: `483 passed,
  209 deselected`. Integration: SKIPPED (docs/harness scope). DOC-IMPACT:
  PASS. The capture pattern kept a deliberate exit 3 in zsh and bash.
- **Commit:** `d0b1899`.
- **Follow-up:** two items of documentation debt found by DOC-IMPACT
  (`CONCEPTS.md` "no commits"; TOOL-RADAR's "DOC-IMPACT not exercised"),
  corrected in H-004b.

## 4D-4: Pipeline integration tests and NUL rejection

- **Status:** complete, 2026-11-11 (reported by the maintainer).
- **Objective:** test the ingestion pipeline end to end against real
  PostgreSQL, below the CLI.
- **Implementation:**
  - `377ce2f`: normalized observations reject U+0000 in any text field and
    anywhere in `raw_data`. The posting becomes `invalid`; it is never
    stripped (SPEC, "Text PostgreSQL cannot store").
  - `62265ac`: integration tests with the real service, collector,
    normalizer, store adapter and repositories, with only HTTP stubbed. Most
    run in one rolled-back transaction; failures are injected by temporary
    triggers; two durability tests commit on unique identifiers and clean up.
    Docstrings in `job_repository.py` and `session.py` were corrected to match
    the code. verify-change's database section lists the new destructive
    operations.
- **Files:** `src/job_radar/domain/job.py`,
  `tests/integration/test_ingestion_pipeline*.py`, `tests/support/pipeline.py`,
  `tests/conftest.py`.
- **Decisions:** reject rather than sanitize invalid text (never invent source
  data). The tests' transaction model is in ARCHITECTURE, "Testing
  boundaries".
- **Verification (reported):** 483 unit and 209 integration tests passed;
  lint, format, mypy and DOC-IMPACT PASS. During the step, one approved
  integration run was started in the background and its result lost; that
  run is unverified (the H-004 motivation).
- **Commits:** `377ce2f`, `62265ac`. The milestone name is recorded in
  TOOL-RADAR and was confirmed by the maintainer; including `377ce2f` is
  inferred (committed 4 seconds before `62265ac`).
- **Follow-up:** lone UTF-16 surrogates are not validated (4D-5).

## H-003: Documentation impact review

- **Status:** complete, 2026-11-11.
- **Objective:** make documentation drift visible in verification.
- **Implementation:** verify-change gains DOC-IMPACT, a read-only, agent-
  judgment review with per-document verdicts. A contradiction the change
  introduces fails the check; older debt is listed without failing it.
  `CLAUDE.md` makes the review mandatory and editing optional.
- **Follow-on audit (`c95f909`):** corrected stale README, ARCHITECTURE and
  SPEC text; added errata to ADR-0006 and ADR-0011, and the erratum rule to
  `DECISIONS/README.md`. ADR-0011's erratum leaves open whether the content
  hash should cover `raw_data`.
- **Verification:** not recorded.
- **Commits:** `5c15d96`, `c95f909`. That `c95f909` belongs to H-003 is
  inferred from its message ("found by DOC-IMPACT audit").

## 4D-3: Ingestion orchestration and persistence adapter

- **Status:** complete, 2026-11-11 (inferred, see Commits).
- **Objective:** an ingestion service that runs a crawl: reconciles
  configured boards, crawls each enabled company in its own transaction,
  isolates failures, records outcomes.
- **Implementation:** `src/job_radar/ingestion/` (service, ports, summary),
  `src/job_radar/persistence/ingestion_store.py` (SQLAlchemy failures become
  `PersistenceError` naming the transaction), new domain errors. `464414f`
  made collector and normalizer messages safe by construction.
- **Decisions:** ADR-0016 addenda: error translation and safe source error
  messages.
- **Verification:** not recorded.
- **Commits:** `cb09f9d`, probably `464414f`. The repository does not map
  them to 4D-3. TOOL-RADAR dates the H-003 audit "after Step 4D-3", and
  `cb09f9d` is the last product commit before it.

## H-002: verify-change skill

- **Status:** complete, 2026-11-10.
- **Objective:** a written verification procedure that reports what was and
  was not verified, and guards the destructive integration suite.
- **Implementation:** `.claude/skills/verify-change/` with conservative scope
  classification, a source-tree check, a database safety gate and approval
  for every integration run.
- **Verification:** not recorded. The motivating audit (169 unit tests run,
  184 integration tests not run) is described in TOOL-RADAR.
- **Commit:** `ef405e0`.

## 4A to 4D-2: Greenhouse collection and ingestion contracts

Evidence is incomplete. Only 4A is named in the history; the other IDs in
this range are not recorded, so the work is listed by commit.

| Commit | Date | Change |
|---|---|---|
| `a10d928` | 2026-09-19 | Step 4A: Greenhouse ingestion contract (SPEC, ADR-0006, ADR-0016) |
| `6bb8b2e` | 2026-09-28 | Greenhouse collector |
| `68ecf2f` | 2026-10-29 | README and roadmap |
| `2faa56d` | 2026-11-10 | Strict Greenhouse normalizer |
| `de88241` | 2026-11-10 | Orchestration and failure contracts (SPEC, ADR-0004, ADR-0008, ADR-0016) |
| `a66a874` | 2026-11-10 | Board completeness validation |

Verification: not recorded, except where a commit message says otherwise.

## Steps 0–3: Baseline

- **Status:** complete, 2026-09-17.
- **Summary:** harness and gates, domain model, PostgreSQL schema and
  migrations, repositories and transaction ownership, atomic upsert,
  concurrency tests, ADR-0001 to ADR-0015.
- **Verification (reported, commit message):** 284 tests passing, 4
  migrations, 15 ADRs.
- **Commits:** `63df80c`; `523b0e7` disables commit trailers.
- **More:** `docs/ai-engineering/LESSONS.md` covers Steps 0–3.
