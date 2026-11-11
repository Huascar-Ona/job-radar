# Project State

Entry point for recovering where Job Radar stands. A snapshot, not a source
of truth: confirm every claim against Git and the code before acting on it.
History is in [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md).

**Last updated:** 2026-11-11, in the H-005 commit. This file
describes the tree of the commit that last changed it
(`git log -1 --format='%h %ad' --date=short -- docs/PROJECT-STATE.md`). Later
commits are not reflected; read them. Uncommitted changes to this file
describe the working tree, not HEAD.

## Current milestone

**None in progress.** Last completed: **H-005**, Context Retrieval
Efficiency. Next planned: **4E**, Ingestion CLI, which needs approval to
start. Its scope is below.

## Roadmap

| Order | Milestone | Status |
|---|---|---|
| 1 | H-004b, Project Traceability & Context Handoff | complete |
| 2 | 4D-5, Lone-surrogate Unicode validation | complete |
| 3 | H-005, Context Retrieval Efficiency | complete |
| 4 | 4E, Ingestion CLI | planned (next) |

Each step starts only with explicit approval. The order was agreed with the
maintainer on 2026-11-11; it is not recorded anywhere else in the repository.

### Scope of open milestones

**4E, Ingestion CLI.**
- Objective: make the existing ingestion service executable from the
  `job-radar` entry point (`pyproject.toml`, `src/job_radar/cli.py`),
  following the existing contracts:
  - the CLI owns the process-level engine and session lifecycle (ARCHITECTURE,
    "Transaction ownership");
  - a run follows SPEC "Ingestion run" with configuration from SPEC
    "Configuration" (ADR-0012);
  - exit code 0 for `success`, `partial_success` and `failed` alike; only an
    unhandled crash is non-zero (SPEC, "Run reporting"; ADR-0008);
  - acceptance tests drive the CLI end to end with stubbed HTTP and a real
    database (ADR-0010; ARCHITECTURE, "Testing boundaries").
- Not yet specified anywhere: the command's name, arguments and output.
  Decide them in 4E; nothing here defines them.
- Out of scope: refactoring the ingestion service, collectors or persistence
  beyond what the CLI needs; new sources (Lever, Ashby); a non-zero exit
  policy for partial failure (a separate decision, SPEC); scheduling, retries
  and concurrency.

## Completed milestones

Most recent first. Commits and evidence are in `DEVELOPMENT-LOG.md`.

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
| Lint, format, mypy, 538 unit tests: PASS; integration SKIPPED (docs/harness scope); DOC-IMPACT: PASS | H-005 tree, before commit (no application code or tests changed since 4D-5) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-005). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy: PASS; 538 unit and 211 integration tests passed; DOC-IMPACT: PASS; overall PASS | 4D-5 tree, before commit (code and tests identical to the commit) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, 4D-5). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy, 483 unit tests: PASS; integration SKIPPED (docs/harness scope) | H-004b tree, before commit | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-004b). The output files were in a session scratchpad and are not preserved |
| 483 unit and 209 integration tests: PASS; lint, format, mypy, DOC-IMPACT: PASS | `62265ac` (4D-4) | Reported by the maintainer; no output is preserved in the repository |
| 284 tests passing | `63df80c` (Step 3) | Commit message |

No continuous integration exists. The last recorded integration run is 4D-5's.
A count above is not evidence for any later tree: run `verify-change`.

## Known issues and open questions

- **Open question:** should the content hash cover `raw_data`? ADR-0011's
  erratum says answering it needs a new ADR.
- **No acceptance tests exist** (ADR-0010 layer 4). They are planned with the
  ingestion CLI (4E).
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
