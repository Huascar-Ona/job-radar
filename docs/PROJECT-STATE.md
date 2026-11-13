# Project State

Entry point for recovering where Job Radar stands. A snapshot, not a source
of truth: confirm every claim against Git and the code before acting on it.
History is in [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md).

**Last updated:** 2026-11-12, at 4E's completion (uncommitted until the 4E commit). This file
describes the tree of the commit that last changed it
(`git log -1 --format='%h %ad' --date=short -- docs/PROJECT-STATE.md`). Later
commits are not reflected; read them. Uncommitted changes to this file
describe the working tree, not HEAD.

## Current milestone

**None in progress.** Last completed: **4E**, Ingestion CLI. No next
milestone has been approved or scoped; agree it with the maintainer before
starting one.

## Roadmap

| Order | Milestone | Status |
|---|---|---|
| 1 | H-004b, Project Traceability & Context Handoff | complete |
| 2 | 4D-5, Lone-surrogate Unicode validation | complete |
| 3 | H-005, Context Retrieval Efficiency | complete |
| 4 | 4E, Ingestion CLI | complete |

Each step starts only with explicit approval. The order was agreed with the
maintainer on 2026-11-11; it is not recorded anywhere else in the repository.
Nothing is planned after it.

## Completed milestones

Most recent first. Commits and evidence are in `DEVELOPMENT-LOG.md`.

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
| Lint, format, mypy, 578 unit tests: PASS; DOC-IMPACT: PASS | 4E tree, after the closing documentation edits, before commit | Captured by verify-change, 2026-11-12 (DEVELOPMENT-LOG, 4E). The output files were in a session scratchpad and are not preserved |
| `make test-integration`, exit 0: 215 integration tests passed, including the 4 CLI acceptance tests | 4E tree, before the closing documentation edits (application code and tests identical to the commit) | Captured by verify-change, 2026-11-12, on the maintainer's single-use authorization (DEVELOPMENT-LOG, 4E). The output file was in a session scratchpad and is not preserved |
| Lint, format, mypy, 538 unit tests: PASS; integration SKIPPED (docs/harness scope); DOC-IMPACT: PASS | H-005 tree, before commit (no application code or tests changed since 4D-5) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-005). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy: PASS; 538 unit and 211 integration tests passed; DOC-IMPACT: PASS; overall PASS | 4D-5 tree, before commit (code and tests identical to the commit) | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, 4D-5). The output files were in a session scratchpad and are not preserved |
| Lint, format, mypy, 483 unit tests: PASS; integration SKIPPED (docs/harness scope) | H-004b tree, before commit | Captured by verify-change, 2026-11-11 (DEVELOPMENT-LOG, H-004b). The output files were in a session scratchpad and are not preserved |
| 483 unit and 209 integration tests: PASS; lint, format, mypy, DOC-IMPACT: PASS | `62265ac` (4D-4) | Reported by the maintainer; no output is preserved in the repository |
| 284 tests passing | `63df80c` (Step 3) | Commit message |

No continuous integration exists. The last recorded integration run is 4E's.
A count above is not evidence for any later tree: run `verify-change`.

## Known issues and open questions

- **Open question:** should the content hash cover `raw_data`? ADR-0011's
  erratum says answering it needs a new ADR.
- **An interrupted `ingest` can leave a crawl run `running`** (ADR-0008
  addendum, 2026-11-10). There is no `interrupted` status or recovery yet.
- **`docs/DECISIONS/README.md` describes errata but not the dated addenda**
  that ADR-0004, 0006, 0008 and 0016 carry. The 4E addendum to ADR-0008 was
  accepted as written; the policy text is unchanged.
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
