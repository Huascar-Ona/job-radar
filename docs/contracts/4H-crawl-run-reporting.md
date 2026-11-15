# 4H contract: crawl run reporting (`job-radar runs`)

- Status: **approved** (maintainer, 2026-11-14)
- Version: 1.0
- Approval: the SHA-256 of this file as approved is recorded in
  `docs/PROJECT-STATE.md` (4H scope) and in the H2 Experiment 001 record;
  any later edit is a new version and needs a new approval.

## Purpose

Let a user inspect recent crawl runs and their per-company results without
writing SQL (README Roadmap, Phase 2: "Add crawl execution metrics and
operational reporting"; ADR-0008: "which boards have been failing this week
is a SQL query"). Read-only. No migration, no ingestion change.

## Data reported (all persisted today; nothing else)

Run level, `crawl_runs`: `id`, `status` (`running`, `success`,
`partial_success`, `failed`), `started_at`, `finished_at`,
`companies_total`, `companies_succeeded`, `companies_failed`, `jobs_fetched`,
`jobs_new`, `jobs_existing`, `jobs_updated`, `jobs_invalid`,
`jobs_deactivated`.

Company level, `crawl_company_results` joined to `companies`: `crawl_run_id`,
the company's `source`, `board_token` and current `display_name`, `status`
(`success`, `failed`), `started_at`, `finished_at`, the same six `jobs_*`
counters, `completeness_verified`, `error_type`, `error_message`.

Derived, never stored: `duration_ms` = `finished_at - started_at`, only when
both exist.

Rules that follow from how the data is written:

- A run's counters are written only when it finishes. For a run whose status
  is `running` (`finished_at` NULL), every run-level counter and the duration
  are reported as unknown (`null` in JSON, `-` in tables), never as the
  stored defaults of 0. It may be in progress or abandoned (ADR-0008
  addendum); the report says `running`, never guesses which.
- `completeness_verified` is reported as stored: `true`, `false` or `null`.
  `null` means not recorded (a failed company, or a result written before
  migration 0005).
- `display_name` is the company's current name, not its name at run time.
- Not reported, because not persisted: deactivation skip reasons and other
  log-only details (ADR-0017, E1), what a failed company fetched before
  failing, per-job details.
- Error text is stored safe by construction (SPEC "Safe error messages",
  bounded to 500 characters at write time). Tables sanitize it like `jobs`
  (`display_text`) and cut it to 60 characters; JSON Lines carries the full
  stored message, never truncated, escaped to ASCII like every JSONL value.

## Interface

```
job-radar runs [--run ID | --company TEXT] [--status STATUS] [--since TIMESTAMP]
               [--limit N] [--format {table,jsonl}]
```

Three views:

| View | Selected by | Rows | Order |
|---|---|---|---|
| Recent runs | default | runs | `id` descending |
| One run | `--run ID` | that run, then all its company results | results by `started_at`, then result `id` |
| Company history | `--company TEXT` | company results across runs, with their run's id and status | run `id` descending, then result `id` |

Options:

- `--run ID`: a positive integer. Mutually exclusive with `--company`.
  `--status`, `--since` and `--limit` are usage errors with it.
- `--company TEXT`: literal, case-insensitive substring of `display_name` or
  `board_token`, exactly as `jobs --company` (`ILIKE` with autoescape). Not
  empty.
- `--status STATUS`: run statuses in the recent-runs view; company statuses
  (`success`, `failed`) in the company view. Any other value is a usage error.
- `--since TIMESTAMP`: rows whose `started_at >= TIMESTAMP` (inclusive); ISO
  8601 with an offset, normalized to UTC, parsed exactly as `jobs --since`.
- `--limit N`: 1 to 200, default 20; applies to runs or company-result rows.
  One extra row is fetched only to report whether more matched.
- `--format table|jsonl`, default `table`.
- Each option may be given once.

Output:

- Table: plain text, no colour or terminal detection, UTC timestamps
  (`YYYY-MM-DD HH:MM:SS`), every database text value sanitized with
  `display_text`, unknown values as `-`. Recent runs: `RUN`, `STATUS`,
  `STARTED (UTC)`, `DURATION`, `COMPANIES` (`succeeded/failed/total`),
  `FETCHED`, `NEW`, `EXISTING`, `UPDATED`, `INVALID`, `DEACTIVATED`. One run:
  a run line, then one row per company with `COMPANY`, `STATUS`, the six
  counters, `COMPLETE` (`yes`, `no`, `-`) and `ERROR` (`<type>: <message>`,
  cut to 60 characters with `...`). Company history: `RUN`, `RUN STATUS`,
  then the company columns. Footer as `jobs` (`<n> runs.` /
  `<n> rows shown; more match (raise --limit, max 200).`); an empty result
  prints only `No matching crawl runs.`, and an unknown `--run` prints
  `No crawl run with id <ID>.`
- JSON Lines (ASCII-only, stdout carries data only): recent runs, one object
  per run; one run, a single object with a `companies` array; company
  history, one object per company result. Keys are the field names above,
  timestamps UTC ISO 8601 with microseconds, unknowns `null`, internal
  company ids omitted.
- stderr: one INFO record `listed <n> ...` with `limit` and `more_available`
  (existing log fields), and nothing else on success.

Behaviour shared with `jobs`: settings via `DatabaseSettings` only; the
read-only, repeatable-read engine and `read_only_session`; database errors
exit 1 through `to_persistence_error` with operation `query_runs`; settings
and URL failures exit 1 as in `jobs`; usage errors exit 2; any other
exception propagates; the engine is always disposed and the session rolled
back.

Exit codes: 0 when the query ran, including empty results and an unknown
`--run` id; 1 and 2 as above.

## Acceptance criteria

1. `job-radar runs` lists at most 20 runs, newest (`id` descending) first,
   as a table; exit 0.
2. Each run row shows id, status, `started_at`, duration, companies
   succeeded/failed/total and the six job counters exactly as persisted.
3. A `running` run shows `running`, no `finished_at`, and unknown (`-` /
   `null`) duration and counters, never 0.
4. `--run ID` shows that run and every company result of it, ordered by
   `started_at` then result id, with the run-level and company-level counters
   kept apart.
5. Each company result shows source, board token, current display name,
   status, the six counters including `jobs_deactivated`, and completeness as
   `yes` / `no` / `-` (`true` / `false` / `null`).
6. A failed company result shows its stored `error_type` and
   `error_message` (cut to 60 characters in tables, complete in JSONL); a
   successful one shows none.
7. `--run` with an id that does not exist prints `No crawl run with id <ID>.`
   (table) or nothing (JSONL) and exits 0.
8. `--company TEXT` lists company results across runs whose display name or
   board token contains TEXT literally, ignoring case (`%`, `_` match
   themselves), newest run first, each row with its run id and run status.
9. `--status` filters run status in the recent-runs view and company status
   in the company view; an invalid value exits 2.
10. `--since T` includes rows with `started_at >= T`; a naive or unparseable
    timestamp exits 2.
11. `--limit` accepts 1–200 (default 20); the footer (table) and the INFO
    record (both formats) report when more rows matched.
12. `--run` with `--company`, `--status`, `--since` or `--limit` exits 2; any
    option given twice exits 2; an empty `--company` exits 2.
13. Table output contains no Cc, Cf, Cs, Zl or Zp character from stored text
    (display names, board tokens, error messages).
14. JSONL output is ASCII-only, carries only data on stdout, uses `null` for
    unknown values, and omits internal company ids.
15. The command reads only `JOB_RADAR_DATABASE_URL`; an invalid HTTP timeout
    or a missing board file does not affect it.
16. It runs in one `REPEATABLE READ, READ ONLY` transaction, which is rolled
    back; the engine is disposed on every path; no HTTP client is created.
17. A database error exits 1 with one `ERROR` record carrying operation
    `query_runs`, the error class and SQLSTATE only.
18. No migration is added; ingestion code and the existing commands'
    behaviour are unchanged; all existing tests still pass.
19. End to end: after `ingest` runs with stubbed HTTP (one success, one
    partial success with one failing board), `runs`, `runs --run <id>` and
    `runs --company <token>` report exactly what those runs recorded.

## Implementation constraints

- Queries in a dedicated `src/job_radar/persistence/run_query.py`; the CLI
  change is limited to the `runs` subcommand and its renderers, with no
  unrelated restructuring of `cli.py`.
- No new dependency, no migration, no change to ingestion or to the
  existing commands.

## Decisions (approved by the maintainer, 2026-11-14)

1. The `--company` history view is kept.
2. An unknown `--run` id exits 0 with an explicitly empty result (the table
   message above; no JSONL line).
3. `--limit` defaults to 20, maximum 200.
4. Error messages are cut to 60 characters in tables; JSONL keeps the full
   stored message.
5. `--run --format jsonl` prints one object with a nested `companies` array.
6. Counters and duration of a `running` run are unknown, never 0.
7. Placement as in "Implementation constraints".
