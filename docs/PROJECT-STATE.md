# Project State

Entry point for recovering where Job Radar stands. A snapshot, not a source
of truth: confirm every claim against Git and the code before acting on it.
History is in [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md).

**Last updated:** 2026-11-14, after H1 (`f3f58ca`), at 4H contract approval. This file
describes the tree of the commit that last changed it
(`git log -1 --format='%h %ad' --date=short -- docs/PROJECT-STATE.md`). Later
commits are not reflected; read them. Uncommitted changes to this file
describe the working tree, not HEAD.

## Current milestone

**4H, Crawl Run Reporting: contract approved** (direction approved
2026-11-13, contract and protocol 2026-11-14). A read-only `job-radar runs` command (README Roadmap, Phase 2:
"Add crawl execution metrics and operational reporting"), to be implemented
as H2 Experiment 001, the first controlled agent-evaluation run. Nothing is
implemented yet. Last completed: **H1** (harness, `f3f58ca`); last product
milestone: **4G**.

## Roadmap

| Order | Milestone | Status |
|---|---|---|
| 1 | H-004b, Project Traceability & Context Handoff | complete |
| 2 | 4D-5, Lone-surrogate Unicode validation | complete |
| 3 | H-005, Context Retrieval Efficiency | complete |
| 4 | 4E, Ingestion CLI | complete |
| 5 | 4F, Job listing query (`job-radar jobs`) | complete |
| 6 | 4G, Deactivation on verified absence | complete |
| 7 | H1, Agent Observability & Efficiency | complete |
| 8 | 4H, Crawl Run Reporting (H2 Experiment 001) | contract approved; held-out tests next |

Each step starts only with explicit approval. Steps 1–4 were agreed with the
maintainer on 2026-11-11, 4F and 4G on 2026-11-12, H1 and 4H on 2026-11-13;
the order is not recorded anywhere else in the repository. Nothing is
planned after 4H.

### Scope of open milestones

**4H, Crawl Run Reporting.**
- Objective: let a user inspect recent crawl runs and their per-company
  results without writing SQL, read-only, reusing the `job-radar jobs`
  patterns; no migration, no ingestion change.
- Status: contract and the H2 Experiment 001 protocol approved on
  2026-11-14. The commit that adds them
  (`git log --format=%h -F --grep='docs(harness): finalize 4H contract and H2 evaluation protocol'`)
  is the experiment's baseline. Next: a separate evaluator session writes
  the held-out acceptance tests outside the repository. Implementation is
  not authorized yet; it is to be done by a separate agent session under
  the approved contract, without access to the held-out tests.
- Approved documents (SHA-256):
  - `docs/contracts/4H-crawl-run-reporting.md` v1.0:
    `788ed0d55adb14b9a6cc1c89aeb20fae22ed988df0d477b9cf260d9c60dcdcea`
  - `docs/metrics/experiments/001-4H-protocol.md` v1.0:
    `0efcf3b9c8201ae9f23f774bf8670d90e46680f8c5ecfec2a2d2bc4a7a46b1cb`


## Completed milestones

Most recent first. Commits and evidence are in `DEVELOPMENT-LOG.md`.

- **H1**: local, read-only Claude Code session metrics per milestone
  (`tools/harness_metrics/`): deduplicated token categories, active and
  wall-clock time, versioned pricing (no rates yet, so costs are unknown),
  local milestone segments, an outcomes ledger, sanitized snapshots for 4E,
  4F and 4G (boundaries reviewed) (`f3f58ca`)
- **4G**: deactivation on verified absence (ADR-0017): absent jobs of a
  successful, completeness-verified crawl are deactivated in the company's
  transaction behind five guards, reactivated on reappearance; migration
  0005; `FetchedBoard` collector contract
  (`git log --format=%h -F --grep='feat(ingestion): deactivate jobs absent from verified crawls'`)
- **4F**: `job-radar jobs`, a read-only listing of stored active jobs with
  `--since`, `--new-in-last-run`, literal filters, table and JSON Lines
  output, on a `REPEATABLE READ, READ ONLY` engine; `DatabaseSettings` for
  database-only commands (SPEC, "Job listing and query")
  (`git log --format=%h -F --grep='feat(cli): add the read-only job-radar jobs command'`)
- **4E**: `job-radar ingest`, the CLI composition root over the ingestion
  service, with a stdout run summary, JSON Lines logs on stderr and
  end-to-end acceptance tests (SPEC, "Command-line interface")
  (`git log --format=%h -F --grep='feat(cli): add the job-radar ingest command'`)
- **H-005**: context freshness rules, milestone scope in this file, volatile
  claims removed from docs and checked by DOC-IMPACT
  (`git log --format=%h -F --grep='feat(harness): improve context freshness and retrieval'`)
- **4D-5**: unpaired UTF-16 surrogates rejected in the domain model, like NUL.
  (`git log --format=%h -F --grep='fix(domain): reject lone Unicode surrogates'`)
- **H-004b**: this file, `DEVELOPMENT-LOG.md`, the `project-handoff` skill.
  The commit that added this file
  (`git log --diff-filter=A --format=%h -- docs/PROJECT-STATE.md`)
- **H-004**: verify-change requires captured evidence; adds UNVERIFIED. `d0b1899`
- **4D-4**: NUL rejection and PostgreSQL pipeline integration tests. `377ce2f`, `62265ac`
- **H-003**: DOC-IMPACT review in verify-change, plus the audit it prompted. `5c15d96`, `c95f909`
- **4D-3**: ingestion orchestration and persistence adapter. `cb09f9d` (the mapping is inferred)
- **H-002**: the verify-change skill. `ef405e0`
- **4A–4D-2**: Greenhouse contract, collector, normalizer, ingestion contracts,
  completeness check, error sanitization. Individual step IDs are not recorded.
- **Steps 0–3**: baseline. `63df80c`

## Verification baseline

| Evidence | What | Provenance |
|---|---|---|
| Lint, format, mypy, 729 unit tests: PASS; DOC-IMPACT: PASS | 4G tree, after the closing documentation edits | Captured by verify-change, 2026-11-12 (DEVELOPMENT-LOG, 4G). The output files were in a session scratchpad and are not preserved |
| `make test-integration`, exit 0: 267 integration tests passed, including migration 0005 up and down, 14 pipeline deactivation tests and 10 CLI acceptance tests | 4G tree, before the closing documentation edits (application code and tests identical to the commit) | Captured by verify-change, 2026-11-12, on the maintainer's single-use authorization (DEVELOPMENT-LOG, 4G). The output file was in a session scratchpad and is not preserved |
| Lint, format, mypy, 657 unit tests: PASS; DOC-IMPACT: PASS | 4F tree, before the closing documentation edits (application code and tests identical to the commit) | Captured by verify-change, 2026-11-12 (DEVELOPMENT-LOG, 4F). The output files were in a session scratchpad and are not preserved |
| `make test-integration`, exit 0: 242 integration tests passed, including the 24 job query tests (read-only enforcement on fresh and reused pooled connections) and 7 CLI acceptance tests | 4F tree, before the closing documentation edits (application code and tests identical to the commit) | Captured by verify-change, 2026-11-12, on the maintainer's single-use authorization (DEVELOPMENT-LOG, 4F). The output file was in a session scratchpad and is not preserved |
| Lint, format, mypy, 578 unit tests: PASS; DOC-IMPACT: PASS | 4E tree, after the closing documentation edits, before commit | Captured by verify-change, 2026-11-12 (DEVELOPMENT-LOG, 4E). The output files were in a session scratchpad and are not preserved |
| `make test-integration`, exit 0: 215 integration tests passed, including the 4 CLI acceptance tests | 4E tree, before the closing documentation edits (application code and tests identical to the commit) | Captured by verify-change, 2026-11-12, on the maintainer's single-use authorization (DEVELOPMENT-LOG, 4E). The output file was in a session scratchpad and is not preserved |
| Lint, format, mypy, 538 unit tests: PASS; integration SKIPPED (docs/harness scope); DOC-IMPACT: PASS | H-005 tree, before commit (no application code or tests changed since 4D-5) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-005). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy: PASS; 538 unit and 211 integration tests passed; DOC-IMPACT: PASS; overall PASS | 4D-5 tree, before commit (code and tests identical to the commit) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, 4D-5). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy, 483 unit tests: PASS; integration SKIPPED (docs/harness scope) | H-004b tree, before commit | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-004b). The output files were in a session scratchpad and are not preserved |
| 483 unit and 209 integration tests: PASS; lint, format, mypy, DOC-IMPACT: PASS | `62265ac` (4D-4) | Reported by the maintainer; no output is preserved in the repository |
| 284 tests passing | `63df80c` (Step 3) | Commit message |

No continuous integration exists. The last recorded integration run is 4G's.
A count above is not evidence for any later tree: run `verify-change`.

## Known issues and open questions

- **Open question:** should the content hash cover `raw_data`? ADR-0011's
  erratum says answering it needs a new ADR.
- **An interrupted `ingest` can leave a crawl run `running`** (ADR-0008
  addendum, 2026-11-10). There is no `interrupted` status or recovery yet.
- **`docs/DECISIONS/README.md` describes errata but not the dated addenda**
  that ADR-0004, 0006, 0008 and 0016 carry. The 4E addendum to ADR-0008 was
  accepted as written; the policy text is unchanged.
- **`job-radar jobs` limitations** (SPEC, "Job listing and query"):
  `--new-in-last-run` attributes jobs to a run by timestamp correlation, not
  a recorded link, so two runs sharing a `started_at` cannot be told apart;
  accents are significant; non-ASCII case-folding follows the database
  locale; wide characters can misalign table columns; JSON Lines consumers
  must display decoded text safely themselves.
- **The verify-change database-safety grep does not cover every integration
  file that writes** (for example `test_job_repository.py`, `test_schema.py`,
  `test_upsert_concurrency.py`). Those writes predate 4F and run in the test
  databases; the gap was noted in 4F, not fixed.
- **Deactivation limits** (ADR-0017): a board without `meta.total` is never
  deactivated; a board whose every posting closes keeps its jobs active (an
  empty board is not proof); a company losing more than half of at least
  five jobs at once keeps them until a later crawl falls under the
  threshold; skip reasons are in logs only.
- **verify-change and project-handoff are followed, not enforced.** Nothing
  stops an agent from skipping them, or from leaving this file stale.

## Safety and authorization

A summary, not the rules. The authoritative text is `CLAUDE.md`
("Verification", "Current scope") and the verify-change skill (hard rules;
section 4 for the database target checks and the approval question). Where
this summary and they differ, they win.

- **PostgreSQL integration tests** create databases and drop schemas. Ask
  before **every** run, naming the target. One approval covers one
  execution, and a run whose result was lost still consumes it.
- **Docker:** never start or stop it; the maintainer runs `make up`.
- **Evidence:** no PASS without a captured exit code and completion output.
- **Maintainer instructions** (given in milestone prompts, not yet written
  in `CLAUDE.md`): commit and push only on explicit authorization for that
  change; never reset, stash, checkout or pull without being asked; do not
  start a milestone without approval.
- **Out of scope:** `CLAUDE.md`, "Current scope".

## Git references

- Branch `master`. The local `origin/master` tracking ref was at `a66a874`
  when this was written. It reflects the last fetch, whose date is not
  recorded, so whether later commits were pushed is unknown.
- No tags.
- Commit trailers are disabled by `.claude/settings.json` (`523b0e7`).

## Authoritative documentation

- Behavior: [`SPEC.md`](SPEC.md)
- Boundaries: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Decisions: [`DECISIONS/`](DECISIONS/README.md) (ADR-0001 to ADR-0016)
- Verification: [`.claude/skills/verify-change/`](../.claude/skills/verify-change/SKILL.md)
- Harness history: [`ai-engineering/TOOL-RADAR.md`](ai-engineering/TOOL-RADAR.md)

## Recovering context

1. `git rev-parse HEAD`, `git status --short`, `git log --oneline -15`.
2. Read this file, then any commits after the one that last changed it.
3. For the history of a specific milestone, read its entry in
   `DEVELOPMENT-LOG.md`. Do not read the whole log.
4. Before changing behavior, read the relevant SPEC sections and ADRs.
5. Treat test counts here as history. Current status needs `verify-change`.
6. To brief another agent, use the `project-handoff` skill.
