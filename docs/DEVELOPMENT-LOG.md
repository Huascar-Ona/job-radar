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
