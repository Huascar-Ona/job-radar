# Greenfield Bootstrap

A reusable workflow for starting a project with a coding agent, derived from
Job Radar Steps 0–3.

**This is not a template to copy.** Job Radar's file layout suits an ingestion
pipeline with one critical SQL statement. The *sequence* and the *gates*
transfer; the specifics should be re-derived for each project.

Labels: **[REPO]** · **[WORKFLOW]** · **[LESSON]** · **[RADAR]**

---

## Shape of the workflow

```
0  need
1  discovery ───────────► evidence, fixtures
2  specification ───────► SPEC.md
3  architecture ────────► ARCHITECTURE.md
4  decisions ───────────► ADRs
5  invariants ──────────► the list that becomes tests + constraints
6  stack
7  harness ─────────────► lint · types · tests · migrations   ◄── gate exists here
8  scaffold
9  ─┐ smallest vertical slice
10  │ verify
11  │ human review
12  │ promote assumptions into checks
    └─────────────────► repeat, autonomy increasing
13 increase autonomy
14 add tooling only against measured problems
```

Stages 0–8 run once. Stages 9–12 are the working loop. Stages 13–14 are
continuous and slow.

**The single most important structural choice:** the harness (7) exists before
the first line of production code (9). Every increment after that lands into
something that can reject it. [LESSON]

---

## Stage 0 — Need

**Objective.** State the problem in prose, before any solution shape.

**Ask.** Who has this problem? What is unacceptable today? What is explicitly
out of scope for the first milestone?

**Artifact.** A paragraph. Job Radar's: *"Discover newly available software
engineering jobs from configured Greenhouse job boards and persist them
reliably"* [REPO: `docs/SPEC.md`].

**Human decides.** All of it.
**Agent can.** Nothing yet.
**Gate.** You can state the need without naming a technology.

**Failure mode.** Jumping to stack selection. Job Radar's stack was chosen at
stage 6, after the specification existed.

---

## Stage 1 — Discovery and external-system research

**Objective.** Replace assumptions about external systems with evidence.

**Ask.** What does the real API return? What is *documented* versus merely
*observed*? What identifiers exist and what is their uniqueness scope? What is
nullable in practice, not just in the schema?

**Artifacts.** Captured fixtures; a written record separating documented
guarantees from observations.

**Job Radar [REPO].** Before writing a normalizer, 3 public Greenhouse boards
were probed — 458 postings. Findings that changed the specification:

- `first_published` exists and was non-null on 458/458 postings. The spec had
  asserted this field did not exist and that `source_published_at` would be
  permanently null. **Wrong, and caught before implementation.**
- `departments`, `offices` and `content` are returned **only** with
  `content=true` — so that parameter is mandatory, not an optimization.
- `content` is HTML-escaped; timestamps carry offsets like `-04:00`, never `Z`.
- Job ids exceed 32-bit range (`8806187002` observed).

Each of those became a specification statement or a test.

**Human decides.** What to trust, and what "verified" means here.
**Agent can.** Probe, aggregate, sanitize fixtures, tabulate findings.
**Gate.** For every field you plan to depend on: documented, observed, or
assumed? Assumptions must be labeled as such.

**Failure modes.**
- Treating observation as guarantee. ADR-0014 [REPO] records that Greenhouse,
  Lever and Ashby **all** leave posting-id uniqueness scope undocumented. Four
  hundred and fifty-eight non-colliding ids is evidence, not a contract.
- Discovering *after* implementing, so the code shapes the understanding.

---

## Stage 2 — Specification

**Objective.** Define required behavior precisely enough that "done" is
checkable.

**Ask.** What are the exact fields and their nullability? What must happen on
repeat? What must *not* happen? What are the acceptance criteria as numbers?

**Artifact.** `SPEC.md`. Job Radar's contains the normalized field table,
counter definitions (`fetched = new + existing + invalid`), and first-run /
second-run acceptance criteria [REPO].

**Human decides.** Scope, non-goals, acceptance criteria.
**Agent can.** Draft; surface ambiguities; enumerate cases the human omitted.
**Gate.** Can each requirement become a test? If not, it is a wish.

**Failure modes.**
- Fields listed without nullability or origin. Job Radar's table records both
  [REPO], which is what later made "unavailable stays null, never inferred"
  enforceable.
- Leaving semantics implicit. Job Radar initially listed `is_active` with no
  rule for when it becomes false — caught in review, resolved by ADR-0004
  deferring deactivation explicitly rather than silently.

---

## Stage 3 — Architecture

**Objective.** Fix the boundaries. Which parts may know what.

**Ask.** What varies independently? What must not depend on what? Who owns
transactions? Where does external data stop being external?

**Artifact.** `ARCHITECTURE.md`.

**Job Radar [REPO].** Source-specific behavior stays inside its collector and
normalizer; nothing downstream depends on Greenhouse structures. Later extended
with an explicit transaction-ownership table.

**Human decides.** Boundaries.
**Agent can.** Draft; identify where a stated boundary is unenforceable.
**Gate.** Every boundary has a *mechanical* enforcement plan. Prose does not
hold.

**Failure mode.** Boundaries stated and never enforced. Job Radar's boundary is
enforced by a test that parses each domain module's AST and fails on a forbidden
import [REPO: `test_domain_boundary.py`]. Without it, the rule would have
survived until the first inconvenient Tuesday. [LESSON]

---

## Stage 4 — Decisions / ADRs

**Objective.** Record decisions with their reasoning and rejected alternatives.

**Ask.** What was the alternative? What evidence decided it? What does this cost
later?

**Artifact.** One file per decision, numbered, immutable once accepted; a
changed decision is a new ADR that supersedes.

**Job Radar [REPO].** 15 ADRs. The valuable ones record what code cannot show:
ADR-0014 records the *undocumented* nature of provider id semantics; ADR-0015
records that `(xmax = 0)` worked correctly and was removed for a different
reason.

**Human decides.** The decision.
**Agent can.** Draft, including a fair statement of the rejected option.
**Gate.** Someone who was not present can reconstruct the reasoning.

**Failure modes.**
- ADRs that record only the conclusion. The alternatives are the content.
- Editing an accepted ADR to match a changed mind. Supersede instead — ADR-0003
  carries a note that an earlier draft's assumption was disproved by live
  verification [REPO].

---

## Stage 5 — Identify invariants

**Objective.** Enumerate what must always be true, before writing code.

**Ask.** What would corrupt data irrecoverably? What must hold under
concurrency? What must hold even if the application is bypassed?

**Artifact.** A list, each with an intended enforcement point.

**Job Radar [REPO].** Identity is `(source, source_job_id)`; `first_seen_at`
never reassigned; missing source timestamps never inferred; a failed company
never touches its stored jobs; counters never negative.

**Human decides.** Which properties are invariants rather than preferences.
**Agent can.** Propose enforcement points; identify which are unenforceable as
stated.
**Gate.** Each invariant names *where* it will be enforced — database, type
system, test, or explicitly "not enforced yet."

**Failure mode.** Confusing preferences with invariants. Over-constraining is
expensive to undo; see Stage 12 on when *not* to promote a rule.

---

## Stage 6 — Select the stack

**Objective.** Choose tools that can express the invariants and give the agent
fast, specific feedback.

**Ask.** Can this stack enforce the invariants from Stage 5? Does it produce
actionable errors? Does it have a strict mode?

**Selection criteria that matter with agents** [LESSON]:

| Criterion | Why |
|---|---|
| Static checking with a strict mode | Catches a class of error before runtime |
| Fast feedback | Slow gates get skipped |
| Precise errors | The agent acts on the message text |
| Real substrate in tests | See ADR-0010 |
| Migrations from day one | Schema drift is silent otherwise |

**Human decides.** The stack.
**Gate.** Every Stage-5 invariant has a plausible enforcement mechanism.

**Failure mode.** Choosing a convenient test substitute. Job Radar rules out
SQLite explicitly [REPO: ADR-0010]: the design depends on `ON CONFLICT`,
`jsonb` and `timestamptz`, and an SQLite-green suite would say nothing about
production. The concurrency incident could not have been reproduced at all.

---

## Stage 7 — Build the verification harness

**Objective.** A single command that decides whether the repository is healthy.
**Before production code exists.**

**Artifact.** Job Radar: `make check` = `ruff check` + `ruff format --check` +
`mypy` (strict) + `pytest` [REPO: `Makefile`].

**Do not weaken the rules to make the first run pass.** In Step 0, four
failures appeared — two lint, one mypy, one genuinely wrong test SQL. Each was
fixed at the source rather than by adding an ignore [WORKFLOW]. A rule relaxed
on day one is never restored.

**Human decides.** Rule strictness; what blocks a merge.
**Agent can.** Configure everything; fix its own violations.
**Gate.** The gate passes on an empty project, and the *test suite can fail* —
verify by breaking something deliberately.

**Failure modes.**
- Adding the harness after code exists; the backlog makes strictness unaffordable.
- A gate nobody runs. It should be one command, and fast — Job Radar's full
  suite runs in about 4 seconds [REPO].

---

## Stage 8 — Scaffold

**Objective.** Project structure, dependency management, local infrastructure.

**Job Radar [REPO].** `src/` layout, `pyproject.toml`, Docker Compose
PostgreSQL, Alembic wired to settings so credentials stay out of version
control, `.env.example`, Makefile.

**Gate.** A clean checkout reaches a green gate with documented commands.

**Failure mode.** Dependencies added "because we'll need them." Job Radar
deliberately deferred `httpx` until the collector needed it, despite it being in
the chosen stack [WORKFLOW].

---

## Stage 9 — Implement the smallest vertical slice

**Objective.** One thin path through every layer, rather than one complete
layer.

**Ask.** What is the smallest change that exercises the full stack and can be
verified end to end?

**Job Radar's slice order** [WORKFLOW]: domain model → schema and migrations →
repositories → (next: collector, normalizer, ingestion). Each step gated and
explicitly approved before the next.

**Tests first.** In Steps 1–3 the tests were written and observed to *fail*
before implementation [WORKFLOW]. With agents this matters more than usual: an
agent asked to write tests for existing code tends to write tests that pass.

**Human decides.** Slice boundaries; what is out of scope for this slice.
**Agent can.** Write the failing tests, implement, iterate to green.
**Gate.** New tests failed first; full gate green; nothing outside the slice
touched.

**Failure modes.**
- Horizontal layers. A complete persistence layer with nothing calling it
  cannot be validated.
- Scope creep inside a slice, which makes the gate ambiguous.

---

## Stage 10 — Verify

**Objective.** Run the whole gate, not the part you changed.

**Also verify the verifiers.** Job Radar has four explicit "guards the guard"
tests [REPO]: an empty-package check that would make boundary tests vacuous; a
deliberate table drop proving `alembic check` can fail; a wrong-column insert
proving constraint tests distinguish `IntegrityError` from other failures; and a
source scan proving a stray `.commit()` would be caught.

**Gate.** Full suite green; every new check demonstrated capable of failing.

**Failure mode.** Tests that cannot fail. A test asserting an empty collection
has no members passes forever.

---

## Stage 11 — Human / design review

**Objective.** Question premises. Not lines.

**Ask** [LESSON]:
- What is assumed here that nobody verified?
- What happens under concurrency? On partial failure? On empty input?
- Is the boundary in the right place?
- **Could these tests fail?**
- What would have to be true for this to be wrong?

**Job Radar [WORKFLOW].** Review at this stage produced: the `observed_at`
rename (a normalizer cannot know persistence history); the `jobs.source`
redundancy investigation; the blank-`source_job_id` gap; and the concurrency
incident. None were visible in a diff.

**Gate.** Every concern is resolved, reproduced as a test, or recorded as a
known limitation.

**Failure mode.** Reviewing what the agent wrote instead of what it assumed.
The linter is better at the former than you are.

---

## Stage 12 — Promote discovered assumptions into checks

**Objective.** Turn each finding into something that runs.

**The pattern** [LESSON]:

```
assumption → challenge → experiment → failing test → correction → permanent verifier
```

**Job Radar examples [REPO]:**

| Assumption | Became |
|---|---|
| "Pydantic rejects blank ids, so the DB is safe" | migration 0003: `CHECK (source_job_id ~ '\S')` |
| "A job's source matches its company's" | migration 0002: composite FK |
| "`prior` reflects what `ON CONFLICT` sees" | migration 0004 + 8 concurrency tests |

**But do not promote every rule.** When `jobs.source` redundancy was
questioned, the response was not to add a constraint — it was to research
provider identity semantics across three APIs first, and only then decide
[REPO: ADR-0014]. **Before strengthening a rule, ask whether a better model
removes the need for it.** [LESSON]

**Gate.** New check demonstrated failing against the old behavior.

**Failure modes.**
- Fixing without a reproducing test first — no proof the bug existed, no
  regression barrier.
- Promoting reflexively, accumulating constraints that encode a model nobody
  revisited.

---

## Stage 13 — Increase autonomy gradually

**Objective.** Widen the agent's scope per checkpoint as verification widens.

**A workable progression** [LESSON]:

| Level | Agent does | Requires |
|---|---|---|
| 1 | One file, human reviews everything | any harness |
| 2 | One slice, human reviews the design | gate covers the slice |
| 3 | Multi-slice, human reviews at boundaries | invariants enforced in the substrate |
| 4 | Sustained work between checkpoints | strong gates + observability |
| 5 | Autonomous loops | all of the above, plus bounded blast radius [EXPERIMENTAL] |

Job Radar operated at level 2, occasionally 3 [WORKFLOW]: each step
specified, implemented, gated, reviewed and explicitly approved.

**Gate.** Autonomy increases only after the gate has caught something real at
the current level.

**Failure mode.** Autonomy as a function of confidence rather than of
verification.

---

## Stage 14 — Add specialized tooling only against a measured problem

**Objective.** Resist the stack.

Job Radar uses **no** Skills, subagents, hooks or MCP servers. There is no
`.claude/` directory [REPO]. None were needed for Steps 0–3.

**Decision criterion** [LESSON]: name the symptom you have *observed*, the
measurement that confirms it, and what the tool would change. If you cannot
state all three, wait.

See `TOOL-RADAR.md`.

**Failure mode.** Cargo-cult agent engineering: installing capability because it
signals sophistication. Every addition is configuration, maintenance, and a new
place for behavior to become unpredictable.

---

## Checklist

```
[ ] Need stated without naming a technology
[ ] External systems probed; documented vs observed vs assumed separated
[ ] SPEC.md: every requirement can become a test
[ ] ARCHITECTURE.md: every boundary has mechanical enforcement
[ ] ADRs record rejected alternatives, not just conclusions
[ ] Invariants listed with enforcement points
[ ] Stack can express the invariants; strict modes enabled
[ ] One gate command, fast, green on an empty project
[ ] Scaffold reaches green from a clean checkout
[ ] Slices are vertical; tests written first and observed failing
[ ] Verifiers proven capable of failing
[ ] Review questions premises, not lines
[ ] Findings promoted into executable checks — after asking if a better model removes the need
[ ] Autonomy follows verification
[ ] No tool added without a measured problem
```
