# ADR-0008: Explicit crawl outcome model

- Status: Accepted
- Date: 2026-09-15

## Context

Failure isolation means a run can partially succeed. "Some companies worked"
is not expressible as a boolean, and logs are not queryable state.

Deferred deactivation (ADR-0004) additionally requires knowing which companies
were crawled *successfully*, which cannot be reconstructed after the fact.

## Decision

Crawl outcomes are persisted, not merely logged.

A run has one status:

| Status | Condition |
|---|---|
| success | every attempted company succeeded |
| partial_success | at least one succeeded and at least one failed |
| failed | at least one company attempted, none succeeded |

A run over zero enabled companies is `success`.

Each company within a run records its own status, counters, and error details.

Status values are stored as text with a CHECK constraint rather than a native
PostgreSQL enum, because adding a value to a native enum is an awkward
migration and more statuses are likely.

Process exit code is explicitly **not** decided here. The MVP exits 0 for all
three statuses; only an unhandled crash exits non-zero. Whether partial failure
should be non-zero is deferred until a scheduler actually consumes the code.

## Consequences

- "Which boards have been failing this week" is a SQL query.
- Future deactivation has the successful-crawl record it requires.
- Outcome rows accumulate per run per company and will need a retention policy
  eventually. Not an MVP concern.

## Addendum, 2026-11-10: how outcomes are written

Approved ahead of implementing the ingestion service. The statuses above are
unchanged.

- The run row is created in the `running` state and committed before the
  first company, and finished after the last.
- A **successful** company's result is written in the same transaction as its
  job writes, so a success record exists exactly when those writes committed.
- A **failed** company's result is written in a new transaction after its own
  transaction rolled back, with zero counters and its error type and safe
  message. What it fetched before failing appears only in the log.
- A run over zero enabled companies records `success` with zero counters.
- **An interrupted run may remain `running`.** If persisting a failed
  company's result itself fails, the run aborts without being finished; a
  crash does the same. No `interrupted` status and no recovery mechanism are
  introduced yet. Consumers of `crawl_runs` must not assume every run reaches
  a terminal status.

## Addendum, 2026-11-11: exit code of a run that does not complete

Approved with the ingestion CLI (Step 4E; SPEC, "Command-line interface").
The decision above is unchanged: `success`, `partial_success` and `failed`
all exit 0, and whether partial failure should be non-zero is still
deferred.

"Only an unhandled crash exits non-zero" is made precise. Non-zero means the
run did not complete. The CLI catches the failures the contract documents,
invalid settings, an invalid board file and a `PersistenceError` raised out
of the run, and reports each as one safe log record with exit code 1, rather
than a traceback that could quote a database URL. Every other exception
still propagates as a crash. No status-based exit code is introduced.

## Addendum, 2026-11-12: deactivated counter and completeness

Recorded with ADR-0017 (Step 4G). The statuses and the exit-code decision
are unchanged. Runs and company results gain a `deactivated` counter
(`jobs_deactivated`): stored jobs a successful crawl found absent and
deactivated. It counts stored jobs, not fetched postings, so it stays outside
`fetched = new + existing + invalid`; a failed company records 0. Company
results also record `completeness_verified` (NULL for a failed company).
Deactivation happens inside the successful company's transaction, so the
success record exists exactly when its deactivations committed.
