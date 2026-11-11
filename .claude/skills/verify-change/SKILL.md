---
name: verify-change
description: Verify the current Job Radar working-tree change with the repository's existing gates (Ruff lint, Ruff format check, mypy strict, unit tests, PostgreSQL integration tests) plus a read-only documentation impact review (DOC-IMPACT), and report each check as PASS/FAIL/BLOCKED/SKIPPED with an overall PASS/FAIL/BLOCKED/PARTIAL. Use before claiming a change is done or verified, before committing, or when asked to verify, run the checks, or run make check.
argument-hint: "[integration=ask|no]"
---

# verify-change

Verify the working-tree change and report evidence. This skill observes and
reports; it never fixes.

It is a procedure, not a security boundary. Its rules are followed by the
agent, not enforced by the harness.

## Hard rules

1. **Never modify the project.** Do not edit source, tests, migrations,
   config, dependencies or docs. Never run `make format`, `ruff check --fix`,
   `ruff format` without `--check`, `make install`, `pip install`,
   `make migrate`, `make revision`, `make downgrade`, `make clean`, `git add`,
   `git commit`, `git stash`, `git checkout` or `git restore`. If something
   needs fixing, put it in the report and stop.
2. **Run only the commands named in this file.** They are the Makefile's own
   recipes, plus read-only inspection, including the read-only review commands
   in section 5a. Do not invent checks. Do not narrow a suite with `-k`, `-x`,
   `--lf`, `--deselect` or paths.
3. **Never start or stop Docker.** Do not run `make up`, `make down` or any
   `docker compose up/down/start/stop`. If PostgreSQL is unavailable, report
   BLOCKED and tell the user they can run `! make up` themselves.
4. **Integration tests need fresh, specific approval.** They create databases
   and drop schemas. Ask in this conversation for every run, naming the exact
   target (section 4). Approval from an earlier run or conversation does not
   count. There is no argument that pre-approves them.
5. **Any doubt about the database target means do not run.** A working
   connection does not mean the target is safe.
6. **Skipped or blocked is never passed.** A required check is PASS only if it
   ran to completion and met its pass condition.
7. **Run every check that can run.** One failure does not stop the others.
   That's why this skill does not use `make check`, which halts at the first
   failing target.

## 1. Record the starting state

Run these read-only commands and keep the output:

```
git rev-parse --short HEAD
git status --porcelain=v1 --untracked-files=all
git diff --stat HEAD
```

If the tree is clean, verify HEAD and say so. A clean tree is still verified;
it does not mean PASS.

## 2. Classify the scope (conservatively)

Classify every path in `git status`. For renames, classify both the old and
the new path; deletions count too.

A path is **docs/harness** only if it matches one of these exactly:

- `docs/**`
- `README.md` (repository root only)
- `CLAUDE.md` (repository root only)
- `.claude/**`

**Every other path is code-affecting.** That includes paths under `src/`,
`tests/` (even `.md` files there), `migrations/`, `config/`, build files,
dotfiles, and anything you don't recognise. Never downgrade verification for a
path you're unsure about.

Required checks:

| Scope | LINT | FORMAT | TYPECHECK | UNIT | INTEGRATION | DOC-IMPACT |
|---|---|---|---|---|---|---|
| Any code-affecting path | required | required | required | required | required | required |
| Only docs/harness paths | required | required | required | required | not required | required |
| Clean tree | required | required | required | required | required | not required |

A clean tree has no change to review, so DOC-IMPACT is SKIPPED there with
reason "no working-tree change". Reviewing a commit range is out of scope.

Flag these as higher-risk scope in the report:

- `migrations/**` or `src/job_radar/persistence/models.py`: schema change.
- `pyproject.toml`: the installed `.venv` may be out of date. Do not reinstall.
- `tests/**` changed alongside `src/**`: review for weakened tests
  (CLAUDE.md, "Do not weaken tests").

## 3. Check tool prerequisites

Check that each tool exists and is executable:

```
test -x .venv/bin/ruff && test -x .venv/bin/mypy && test -x .venv/bin/pytest
```

If one is missing, every check that needs it is **BLOCKED** with remediation
"run `make install`". Do not run it yourself.

### 3a. Confirm the source tree under test

Verification must run against the tree being changed. `.venv` holds an
editable install of `job_radar` that points at one checkout's `src/`. A
virtualenv shared with, or symlinked from, another checkout or git worktree
imports that other tree's code, so tests would exercise code other than the
change. Ruff and mypy read files from the current directory and are not
affected. pytest imports through the virtualenv and is.

```
git rev-parse --show-toplevel
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import job_radar, pathlib; print(pathlib.Path(job_radar.__file__).resolve())"
```

The source tree matches only if the resolved path is inside the resolved
`<toplevel>/src/`. Otherwise mark UNIT and INTEGRATION **BLOCKED** and do not
run them:

- Path outside the tree: reason "virtualenv imports `job_radar` from another
  tree: <path>". Remediation: run from the checkout the virtualenv was
  installed for, or give this tree its own virtualenv.
- Import fails or either value cannot be determined: reason "cannot confirm
  source tree", with the error's first `<module>.<Name>Error: ...` line as
  evidence. Remediation: `make install`.

Do not reinstall or reconfigure the virtualenv yourself. Never report PASS
for tests that imported code from a different checkout.

## 4. Check database prerequisites (only if integration will be attempted)

Attempt integration if it is required, or if the user explicitly asked for it.
If the invocation said `integration=no`, mark INTEGRATION **SKIPPED** and skip
this section.

### 4a. Effective target, without credentials

`Settings` (`src/job_radar/settings.py`) resolves `JOB_RADAR_DATABASE_URL` in
this order: environment variable, then `.env`, then the built-in default.
Report which one applied, and the host, port and database:

```
printenv JOB_RADAR_DATABASE_URL >/dev/null && echo "source: environment" || { test -f .env && echo "source: .env or default (.env present)" || echo "source: default (.env absent)"; }
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from job_radar.settings import Settings; from sqlalchemy.engine import make_url; u = make_url(Settings().database_url); print(u.drivername, u.host, u.port, u.database)"
```

Never print the URL itself, the password or the contents of `.env`.

### 4b. Confirm what the suite will do to that server

Re-read the code each time; do not trust this file's description over it:

```
grep -n "_DB = " tests/conftest.py
grep -n "CREATE DATABASE\|DROP SCHEMA\|DROP TABLE" tests/support/database.py
grep -n "CREATE FUNCTION\|CREATE TRIGGER\|DELETE FROM\|UPDATE companies" tests/conftest.py tests/support/pipeline.py tests/integration/test_ingestion_pipeline*.py
```

When this skill was written, these were:

- Test databases `jobradar_schema_test` (`SCHEMA_DB`) and
  `jobradar_migration_test` (`MIGRATION_DB`). They are derived from the
  configured URL with the database name replaced, on the same host and port.
- `ensure_database` runs `CREATE DATABASE` if the database is missing.
- `drop_all_tables` runs `DROP SCHEMA public CASCADE` in each test database
  at session start. Migration tests also upgrade, downgrade and drop tables.
  Concurrency tests commit rows.
- Ingestion pipeline tests (`tests/integration/test_ingestion_pipeline*.py`)
  run in `jobradar_schema_test` only, and assert that with
  `current_database()` before writing. Most hold one transaction that is
  always rolled back (`pipeline_connection` in `tests/conftest.py`): inside it
  they disable every company, and the failure tests create temporary
  functions and triggers (`install_failing_trigger` in
  `tests/support/pipeline.py`). The two durability tests commit for real, but
  only after checking that no company is enabled and that their identifiers
  are unused, and then `DELETE` only the rows carrying those identifiers.
- The configured database itself is used only by
  `tests/integration/test_database_connection.py`.

If the grep output differs from this list, report the difference and **do not
run integration tests** until the user has reviewed it.

### 4c. Safety gate

Do not run integration tests, and mark INTEGRATION **BLOCKED**, if any of these
holds:

- the host is not `localhost`, `127.0.0.1` or `::1`;
- the driver is not `postgresql+psycopg`;
- the configured database name equals either test database name;
- either test database name lacks the `_test` suffix;
- any value above could not be determined.

The reason in the report is "unverified or unexpected database target".

### 4d. Connectivity (read-only)

```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from job_radar.settings import Settings; from job_radar.persistence.session import create_database_engine, check_connection; print(check_connection(create_database_engine(Settings().database_url)))"
```

This runs `SELECT 1` and nothing else.

If it raises, INTEGRATION is **BLOCKED**:

- Record the first line of the form `<module>.<Name>Error: ...`, with any
  credentials removed. For example:
  `psycopg.OperationalError: connection failed: ... Connection refused`.
  Not the last traceback line, which is only SQLAlchemy's
  "Background on this error" link.
- To help diagnose, you may run the read-only `docker compose ps`.
- Tell the user they can start the database with `! make up`.

### 4e. Ask

Only after 4a–4d pass, ask in so many words:

> Integration tests will connect to `<host>:<port>`, run `CREATE DATABASE` if
> missing and `DROP SCHEMA public CASCADE` in `jobradar_schema_test` and
> `jobradar_migration_test`, and read from `<database>`. Run
> `make test-integration` now?

- Yes: run it.
- No, or any other answer: INTEGRATION is **SKIPPED**, reason "omitted by
  user".

## 5. Run the checks

Run each check, recording the exact command, exit code and summary line, and
keep going after failures.

| Check | Command | PASS when |
|---|---|---|
| LINT | `.venv/bin/ruff check .` | exit 0 |
| FORMAT | `.venv/bin/ruff format --check .` | exit 0 |
| TYPECHECK | `make typecheck` | exit 0 and output contains `Success:` |
| UNIT | `make test-unit` | exit 0 and at least 1 passed |
| INTEGRATION | `make test-integration` | exit 0 and at least 1 passed |

Record the exit code of the command exactly as invoked. For `make` targets
that is make's own code, which is 2 whenever the recipe fails. Also record the
underlying tool's code from make's `*** [<target>] Error N` line, and classify
pytest results by that pytest code, never by make's. For example:
`make test-unit` exit 2 (pytest 1) is a FAIL with a pytest exit of 1.

LINT and FORMAT are the two lines of `make lint`, run separately so each
gets its own status. The installed Ruff also format-checks Markdown files, so
a docs-only change can fail FORMAT.

How to read pytest results:

| Result | Status | What to record |
|---|---|---|
| pytest exit 1 | FAIL | Every `FAILED` / `ERROR` node id |
| pytest exit 5 (no tests collected) | FAIL | A gate that tests nothing has not passed |
| pytest exit 2, 3 or 4 | FAIL | Quote the error |
| Integration errors that are all connection failures at setup | BLOCKED | Quote one; not FAIL |
| `skipped`, `xfailed` or `xpassed` in an otherwise passing run | PASS | List them under "Not verified" (the suite currently has none, so any are new) |

These caches are git-ignored and may be written: `.ruff_cache`,
`.mypy_cache`, `.pytest_cache`, `__pycache__`.

## 5a. Documentation impact (DOC-IMPACT)

Check that the documentation still describes the changed tree, without
requiring edits it does not need. This is **agent judgment, not a
deterministic check**: say so in the report, and never claim a document is
consistent unless you read the relevant section.

Never edit documentation here (hard rule 1). A needed update is a finding.

Only these read-only commands, besides reading files:

```
git diff HEAD -- <path>
git show HEAD:<path>
grep -rn -- '<term>' docs README.md CLAUDE.md .claude
```

### Steps

1. **Changed paths.** Every path from section 1, tracked or untracked. Read
   untracked files in full; `git diff HEAD` does not show them.
2. **Candidate documents.** Take the rows that match a changed path. Then
   search the documents for terms from the change: class, function and
   constant names, error messages, operation names, status values, commands.
   Record the terms you searched for.

   | Changed path | Review at least |
   |---|---|
   | `src/job_radar/collectors/**` | SPEC: Greenhouse field mapping, completeness, Safe error messages. ARCHITECTURE: Collector and normalizer responsibilities. ADR-0006, ADR-0016 |
   | `src/job_radar/persistence/**`, `migrations/**` | SPEC: Idempotency, Update semantics, stored columns. ARCHITECTURE: Persistence, Transaction ownership. ADR-0002, ADR-0011, ADR-0013, ADR-0015 |
   | `src/job_radar/ingestion/**` | SPEC: Ingestion run through Structured logging. ARCHITECTURE: diagram, Reliability, Transaction ownership. ADR-0004, ADR-0008, ADR-0016 |
   | `src/job_radar/domain/**` | SPEC: observation fields, Timestamps. ARCHITECTURE: Timestamps. ADR-0003 |
   | `config/**`, `src/job_radar/settings.py` | SPEC: Configuration. ADR-0012. README: Getting Started |
   | `Makefile`, `pyproject.toml`, `docker-compose.yml`, `.env.example` | README: Getting Started, Quality Checks. CLAUDE.md. This skill |
   | `.claude/**` | CLAUDE.md. `docs/ai-engineering/TOOL-RADAR.md` |
   | `tests/**` | ARCHITECTURE: Testing boundaries, only if a test layer changes |
   | A capability completed or scope changed | README: Project Status, Roadmap. CLAUDE.md: Current scope |
   | A changed document | That document, against the code it describes |

3. **Verdict per document.** Read the relevant sections, then give exactly one:

   | Verdict | Meaning |
   |---|---|
   | `updated` | Changed in this tree, and consistent with the code |
   | `unchanged-accurate` | Not changed, still accurate; give the reason |
   | `contradiction-introduced` | Contradicts the changed tree, and was accurate at HEAD |
   | `debt` | Already inaccurate at HEAD, for reasons this change does not touch |
   | `cannot-evaluate` | The claim cannot be checked from the repository; give the reason |

   To tell `contradiction-introduced` from `debt`, compare against HEAD with
   `git show HEAD:<path>`: of the document, and of the code it describes. A
   changed document that contradicts the code is `contradiction-introduced`,
   even if the code did not change.

   A contradiction must be demonstrable: quote the document and point at the
   code, test or file it disagrees with. Missing detail is not a
   contradiction unless the document claims the opposite.

4. **Status.**
   - **FAIL**: any `contradiction-introduced`.
   - **BLOCKED**: otherwise, any `cannot-evaluate` on an important contract:
     an invariant, identity, timestamps, a transaction or error contract, or a
     safety rule.
   - **PASS**: otherwise. Every candidate document is `updated`,
     `unchanged-accurate` or `debt`.

   `debt` never fails the gate. List it separately, so it stays visible
   without blocking unrelated work.

## 6. Overall result

Each check has exactly one status:

- **PASS**: ran and met its pass condition.
- **FAIL**: ran and did not meet it.
- **BLOCKED**: could not run because a prerequisite was missing or unsafe.
- **SKIPPED**: deliberately not run, either not required or omitted by
  decision.

Apply these rules in order. The first match wins.

1. **FAIL** if any check failed. That includes a non-required check that ran
   anyway: failing evidence is never ignored. List any BLOCKED and SKIPPED
   checks as well.
2. **PASS** if every required check passed.
3. **BLOCKED** if every required check was BLOCKED, so nothing required could
   execute.
4. **PARTIAL** otherwise. That covers:
   - some required checks passed and others were BLOCKED or SKIPPED;
   - every required check was deliberately SKIPPED;
   - a mix of BLOCKED and SKIPPED with no PASS.

Non-required checks that were SKIPPED never prevent PASS.

DOC-IMPACT is one more check under these rules, with the same statuses. It
changes no other check's status.

## 7. Confirm nothing changed

Run `git status --porcelain=v1 --untracked-files=all` again and compare it with
section 1. Any difference is a defect of this run. Report it prominently.

## 8. Report

Fill in `report-template.md` (in this directory) and print it.

- Quote commands, exit codes and pytest/mypy summary lines verbatim.
- Never reuse numbers from README.md or from an earlier run.
- Label DOC-IMPACT as agent judgment. List every document and section
  reviewed, the search terms, each verdict with its reason, and any debt.
- If the overall result is not PASS, the first line of your reply must say
  what is unverified. Do not describe the change as "done" or "verified".
