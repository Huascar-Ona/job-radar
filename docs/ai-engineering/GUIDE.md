# AI-Native Engineering: Methodology

Derived from building Job Radar with a coding agent, Steps 0 through 3.

> **Evidence labels used throughout this directory**
>
> - **[REPO]** — verifiable in this repository: code, tests, migrations, ADRs.
> - **[WORKFLOW]** — observed while working, not provable from files alone.
> - **[LESSON]** — generalization; the reasoning transfers, the specifics may not.
> - **[RADAR]** — investigated or worth evaluating. **Not in use here.**
> - **[EXPERIMENTAL]** — frontier. Not established practice for this project.
>
> If a claim carries no label, treat it as [LESSON].

---

## The central idea

The objective is not to make the agent incapable of mistakes.

The objective is to build an environment where **important classes of mistakes
become observable, reproducible, and increasingly hard to introduce.**

This reframes the work. You are not reviewing output. You are engineering the
conditions under which output is produced and checked. The agent is a
probabilistic component; everything around it should be deterministic.

```
            probabilistic implementation
                        |
    ┌───────────────────┴───────────────────┐
    │   deterministic constraints & feedback │
    │   types · lint · tests · DB constraints│
    │   migrations · specs · invariants      │
    └────────────────────────────────────────┘
```

A model that is right 95% of the time, inside a harness that catches 95% of the
remaining 5%, is a different engineering proposition from the same model alone.
The leverage is in the second number, and the second number is *yours*.

---

## The loop

```
human intent
    ↓
discovery / research ─────────────┐
    ↓                             │
specification                     │
    ↓                             │
architecture                      │
    ↓                             │
explicit invariants               │
    ↓                             │
agent implementation              │
    ↓                             │
deterministic verification        │
    ↓                             │
human / design review             │
    ↓                             │
new hypothesis ───────────────────┤
    ↓                             │
reproduction                      │
    ↓                             │
new executable check              │
    ↓                             │
stronger harness ─────────────────┘
```

The loop is not linear. The return edge — hypothesis, reproduction, new check —
is where the harness actually gets stronger. A project that only ever runs the
downward path accumulates code; a project that runs the return edge accumulates
*guarantees*.

### The loop in Job Radar

| Stage | What it looked like here |
|---|---|
| Intent | "Discover software engineering jobs from Greenhouse boards and persist them reliably" [REPO: `docs/SPEC.md`] |
| Discovery | Probed 3 public Greenhouse boards, 458 postings, before writing the normalizer [REPO: `tests/fixtures/greenhouse/README.md`] |
| Specification | Field list, counters, acceptance criteria [REPO: `docs/SPEC.md`] |
| Architecture | Collector/normalizer boundary, transaction ownership [REPO: `docs/ARCHITECTURE.md`] |
| Invariants | `UNIQUE (source, source_job_id)`, `first_seen_at` never reassigned, per-company failure isolation [REPO: ADR-0002, ADR-0003, ADR-0008] |
| Implementation | Agent writes tests first, then code [WORKFLOW] |
| Verification | `make check` = Ruff + mypy strict + pytest against real PostgreSQL [REPO: `Makefile`] |
| Review | Human questions premises, not lines [WORKFLOW] |
| Return edge | See the case study below |

---

## "Tests are green" ≠ "correctness has been proven"

Green tests prove that **the properties you thought to check** hold under **the
conditions you thought to create**. They say nothing about properties you did
not think of, and nothing about conditions you did not construct.

This is not an argument against tests. It is an argument about what a green
suite licenses you to conclude, and about where human attention is worth
spending.

### Case study: the Step 3 concurrency incident

The most instructive thing that happened while building Job Radar.

**Before.** **274 tests passing.** The job upsert classified its own
outcome — inserted, unchanged, updated, or company-conflict — using a CTE that
read the row's prior state alongside the upsert:

```sql
WITH prior AS (SELECT id, company_id, content_hash FROM jobs WHERE ...),
     upserted AS (INSERT ... ON CONFLICT ... DO UPDATE ...
                  WHERE jobs.company_id = EXCLUDED.company_id
                  RETURNING id, (xmax = 0) AS inserted)
SELECT ... FROM upserted FULL OUTER JOIN prior ON true
```

Idempotency itself was never in doubt: `UNIQUE (source, source_job_id)` is the
concurrency primitive and every test of it passed.

**The challenge.** A human asked a question the tests could not answer: *can
`prior` be stale relative to the row `ON CONFLICT` actually resolves against?*

Note what this is. Not "is this line wrong." It is a question about **snapshot
semantics** — a property of the substrate, invisible in the diff, and untested
because no test created the conditions.

**The experiment.** Rather than reasoning to a conclusion, the hypothesis was
put to PostgreSQL. A deterministic race harness held writer A's transaction open
until writer B was *observably blocked*, by polling `pg_stat_activity` for a
lock wait rather than sleeping [REPO: `tests/support/concurrency.py`].

**The result.** The race was real, and it was worse than suspected:

| Scenario | Old behavior | Correct behavior |
|---|---|---|
| Same content, concurrent | reported `UPDATED` | `UNCHANGED` |
| Different company, concurrent | **raised `NoResultFound`** | `COMPANY_CONFLICT` |

Every CTE in one statement shares that statement's snapshot. `ON CONFLICT` does
not — it must see rows committed after the snapshot, or it could not enforce
uniqueness. So `prior` returned nothing while the conflict branch updated a row
a concurrent writer had just committed. In the different-company case, `prior`
was empty *and* the `WHERE` skipped the update, so the `FULL OUTER JOIN`
produced zero rows and `.one()` raised.

The second bug was the serious one: the anomaly the design exists to *report*
became an exception. Nobody had predicted it.

**The correction.** 5 of 8 new tests failed against the old implementation
[WORKFLOW: observed; reproducible by reverting `0004`]. Only then was the
implementation changed: migration `0004` added `previous_content_hash`,
captured inside the conflict branch where `jobs.*` is the live row under the
lock `ON CONFLICT` already holds [REPO: `migrations/versions/0004_*.py`,
ADR-0015]. **284 tests** passing afterward [REPO: verified].

**Why this is success, not failure.**

The 274 green tests were not useless — they were *load-bearing*. They made the
correction safe: the fix touched the single hottest code path in the system, and
the existing suite proved nothing else broke. What they could not do is
anticipate a property nobody had articulated.

The loop worked exactly as designed:

```
assumption:  "prior reflects what ON CONFLICT sees"
    ↓
challenge:   human review questions snapshot semantics
    ↓
experiment:  deterministic race against real PostgreSQL
    ↓
failing test: 5 of 8 fail against current implementation
    ↓
correction:  previous_content_hash, migration 0004
    ↓
permanent verifier: 8 concurrency tests, now part of the gate
```

The harness is permanently stronger. That class of bug cannot return silently.

**The discipline that made it work:** the failing test came *first*. Had the
implementation been "fixed" on the strength of the argument alone, there would
be no proof the bug existed, no proof the fix addressed it, and no regression
barrier. [LESSON]

---

## Evolve the harness gradually

The temptation after reading about agentic engineering is to install a stack:
Skills, subagents, MCP servers, orchestration, memory tools, custom hooks.

Job Radar has **none of these** [REPO: no `.claude/` directory exists]. What it
has is: a type checker in strict mode, a linter with a broad rule set, a test
suite that runs against the real database engine, a migration tool, and written
specifications and decisions. That is the whole harness, and it caught a
snapshot-isolation bug.

Each addition to a harness has a cost: configuration, maintenance, and — most
expensively — a place for the agent's behavior to become unpredictable. Add
capability when a **measured** problem justifies it:

| Symptom you actually observe | Consider |
|---|---|
| Re-explaining the same procedure every session | A Skill |
| Discovery reading swamps the context window | Subagents for search |
| Repeated manual steps after every change | A hook |
| The agent needs data it cannot reach | An MCP server |
| Structural exploration is repeatedly expensive | Codebase indexing [RADAR] |

Not one of those symptoms was reached in Steps 0–3. See `TOOL-RADAR.md` for
what was considered and why it was deferred.

**Sequence that worked here:** executable feedback → written specification →
explicit invariants → small vertical slices → gated increments. Autonomy grew
because verification grew, not the other way round.

---

## What human review becomes

As the harness strengthens, line-by-line review stops being the highest-value
use of human attention — the linter, type checker, and test suite are simply
better at it. Attention shifts upward:

- **Intent.** Does this solve the stated problem, or a nearby easier one?
- **Missing assumptions.** What is being taken as given that nobody verified?
- **Architecture.** Is the boundary in the right place?
- **Boundary conditions.** Concurrency, failure, empty, absent, duplicate.
- **Adversarial scenarios.** What would have to be true for this to be wrong?
- **Is the verification itself sufficient?** Can these tests fail at all?

The concurrency incident came from the fourth and sixth of these. No amount of
diff-reading would have produced it. [LESSON]

---

## Reading order

| Document | Purpose |
|---|---|
| `CONCEPTS.md` | Vocabulary, and how model/agent/harness/context relate |
| `PROJECT-BOOTSTRAP.md` | Greenfield workflow, stage by stage |
| `EXISTING-PROJECT.md` | Brownfield and multi-repository path |
| `TOOL-RADAR.md` | What exists, what it costs, when it becomes justified |
| `LESSONS.md` | Chronological log of what was actually learned |

These describe **methodology**. `SPEC.md`, `ARCHITECTURE.md`, `CLAUDE.md` and
the ADRs describe **Job Radar**. Keeping the two separate is deliberate: the
product documents are the case study, not the lesson.
