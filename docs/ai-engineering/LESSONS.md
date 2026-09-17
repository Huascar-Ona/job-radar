# Engineering Log: Lessons from Steps 0–3

Chronological. Each entry records what happened, what it cost, and what
generalizes.

Labels: **[REPO]** verifiable here · **[WORKFLOW]** observed · **[LESSON]**
generalization.

---

## 1. Executable feedback before autonomy

**What happened [WORKFLOW].** Step 0 built nothing but scaffolding: Ruff, mypy
strict, pytest, PostgreSQL via Docker Compose, Alembic. No production code.
The first gate run produced four failures — two lint, one mypy, one genuinely
wrong SQL in a test — each fixed at the source rather than silenced.

**Why it mattered.** Every subsequent step landed into something that could
reject it. The agent could iterate without a human relaying results: run the
gate, read the failure, fix, re-run.

**[LESSON].** Build the gate before the first line of production code. A gate
added later inherits a backlog, and the backlog is always used to justify
weakening the rules.

**Corollary.** Do not weaken a rule to make the first run pass. One rule relaxed
on day one is never restored.

---

## 2. Tests must be capable of failing

**What happened [REPO].** Four checks exist solely to prove other checks are not
vacuous:

| Guard | What would otherwise pass for the wrong reason |
|---|---|
| `test_domain_package_is_not_empty` | An empty package makes every boundary check trivially true |
| Deliberate table drop → `alembic check` must fail | A drift check that can never fail proves nothing |
| Bad-column insert → must raise `DBAPIError`, not `IntegrityError` | 28 constraint tests could pass by raising the wrong error |
| Source scan for `.commit()` | The rollback test could pass by accident of ordering |

**[LESSON].** For every verifier, ask: *what would make this pass while the
property is false?* Then write the test that fails. This costs minutes and is
the difference between a suite that measures something and one that decorates.

---

## 3. Domain time versus persistence time

**What happened [REPO].** `NormalizedJob` originally carried `first_seen_at` and
`last_seen_at`. Review objected: a normalizer sees one payload. It knows when it
looked. It cannot know whether Job Radar has seen that job before.

Both fields were replaced by `observed_at`. Persistence derives the stored
columns:

```
INSERT:              first_seen_at = observed_at
                     last_seen_at  = observed_at
ON CONFLICT UPDATE:  first_seen_at preserved, never assigned
                     last_seen_at  = observed_at
```

`observed_at` is **not** a column; a test asserts its absence [REPO].

**[LESSON].** When a model carries a field it cannot compute, the field is
misplaced. The question that exposes it: *what does this component actually
know?* Only the database knows whether a row already existed, so only the
database can decide `first_seen_at`.

A second, smaller lesson: the change removed `last_seen_at` too, because the
stated mapping derived it from `observed_at` — leaving both would have created
two fields that must always agree, which is a defect waiting to happen.

---

## 4. Move invariants down when the layer below can hold them

**What happened [REPO].** `source_job_id` was already rejected when blank by
Pydantic: `strip_whitespace=True` runs before `min_length=1`, so `""`, `"   "`
and `"\t\n"` all fail — verified by execution, not by reading the code.

PostgreSQL accepted all three. Any write path that was not the domain model —
raw SQL, a future bulk load, a buggy repository — could store an identity with
no content.

Migration `0003` promoted the invariant into the database.

**Choosing the constraint [REPO].** Candidates were tested against real
PostgreSQL rather than reasoned about:

| Input | `~ '\S'` | `btrim(v) <> ''` | `btrim(v, E' \t\r\n') <> ''` |
|---|---|---|---|
| `'123'` | ✅ | ✅ | ✅ |
| `''` | ❌ | ❌ | ❌ |
| `'   '` | ❌ | ❌ | ❌ |
| tab | ❌ | **✅ wrong** | ❌ |
| newline | ❌ | **✅ wrong** | ❌ |

`btrim`'s default character list is spaces only, so the obvious formulation
would have admitted tab- and newline-only ids. `source_job_id ~ '\S'` — "contains
at least one non-whitespace character" — is the simplest formulation that is
correct: one operator, no character list to get wrong.

**[LESSON].** Validation at the boundary and enforcement in the substrate are
different guarantees. The first protects the path you wrote; the second protects
every path. And when choosing a constraint, test the candidates — the plausible
one was wrong on two of five inputs.

---

## 5. But do not promote every invariant reflexively

**What happened [REPO].** The sequence around `jobs.source`:

1. Noticed that `jobs.source` and `companies.source` could disagree — nothing
   stopped a Lever job referencing a Greenhouse company.
2. Implemented migration `0002`: composite FK `(company_id, source) →
   companies (id, source)`, with `UNIQUE (id, source)` on companies.
3. **Then questioned whether `jobs.source` should exist at all.** If identity
   were `(company_id, source_job_id)`, the column and the constraint both
   disappear.
4. Rather than deciding on taste, researched real provider identifier semantics
   across Greenhouse, Lever and Ashby.
5. Kept the column, with the reasoning recorded in ADR-0014.

**Why it was kept.** The two failure modes are not symmetric. Provider-scoped
identity fails by reassigning one row's `company_id` — visible, self-correcting.
Company-scoped identity fails by emitting the same posting twice — and for a
product whose output is "here are the new jobs," duplicates corrupt the product.

**[LESSON].** Before strengthening a rule, ask whether a better model removes
the need for the rule. Step 3 here — questioning a constraint *just implemented*
— is the one most often skipped, because the work feels finished.

**Not free.** Had step 3 changed the answer, migration `0002` would have been
wasted work. That is the cost of implementing before questioning; the
alternative ordering has its own cost.

---

## 6. Documented guarantees, observations, and assumptions are three things

**What happened [REPO: ADR-0014].** Investigating provider identifier semantics
across three APIs produced exactly one documented guarantee:

> **Documented.** Greenhouse states that `id` identifies the job *post* while
> `internal_job_id` identifies the *job*, and that prospect posts carry a null
> `internal_job_id`.

That is all. **No provider documents the scope within which its posting id is
unique.** Lever says only `id: Unique job posting ID`. Ashby does not document
the `id` field at all, though its API returns one.

> **Observed** (~1,500 postings, 8 boards): no cross-board collisions; Greenhouse
> ids interleave across boards — merging two boards' 305 ids yields 91 board
> transitions, the signature of a shared sequence — but appear in two distinct
> formats, so the space is sharded rather than uniform; Lever and Ashby ids are
> UUID-shaped in 100% of samples.

> **Cannot assume:** uniqueness beyond one board; immutability; that a reposted
> job keeps its id; that formats stay uniform.

The decision was then made on **consequences**, not on a guarantee nobody
offered.

**[LESSON].** Observation is evidence, never contract. Recording the three
categories separately is what lets a later reader — human or agent — know which
conclusions are safe to build on. An agent reading only "ids are unique per
provider" would treat it as fact.

**Operational consequence [REPO: ADR-0014].** Because the premise is unprovable,
a `company_id` change on an existing identity must be treated as an *anomaly* —
surfaced and counted, never silently reassigned. That converts an unverifiable
assumption into an observable event.

---

## 7. Database constraints as concurrency primitives

**What happened [REPO].** Job identity is enforced by `UNIQUE (source,
source_job_id)`, and every write is a single `INSERT ... ON CONFLICT`.

**Why read-then-write is insufficient.** Under concurrency:

```
Writer A                     Writer B
SELECT → absent
                             SELECT → absent
INSERT → ok
                             INSERT → duplicate or crash
```

Both readers see "absent" because neither has committed. The window cannot be
closed by ordering; it can only be closed by an atomic operation or a lock.
`INSERT ... ON CONFLICT` is atomic: one writer inserts, the other takes the
conflict branch.

**Proven, not assumed [REPO].** A test runs two writers against one identity
behind a `threading.Barrier` and asserts exactly one row and exactly one
`INSERTED`.

**[LESSON].** Idempotency belongs in the substrate that can enforce it
atomically. Application-level idempotency is a convention; a unique constraint
is a guarantee.

---

## 8. Transaction ownership is an architectural decision

**What happened [REPO].** Repositories never call `commit()` or `rollback()`.
They execute statements on a `Session` handed to them. Ownership:

| Layer | Owns |
|---|---|
| CLI | process-level session lifecycle |
| Ingestion service | **one transaction per company**, plus run-level writes |
| Repositories | no transaction |

**Why repository-local commits would break failure isolation.** The spec
requires one company's failure not to affect others. That works only if each
company's writes form one unit that can be rolled back independently. A
repository that committed internally would make a company's partial writes
permanent before its crawl finished — and a later failure in that same company
could no longer be undone.

**Enforced twice [REPO]:** a rollback test proving nothing survives, and a
source scan for `.commit()` — because the first could pass by accident of
ordering.

**[LESSON].** "Who owns the transaction" is a design decision with a stated
answer, not an implementation detail discovered per call site.

---

## 9. 274 green tests did not prove the classification was correct

The central incident. Detailed narrative in `GUIDE.md`; recorded here for the
log and for what it teaches.

**The construct.** The job upsert classified its own outcome using a CTE reading
prior state alongside the upsert, plus `(xmax = 0)` to distinguish insert from
update, plus `WHERE jobs.company_id = EXCLUDED.company_id` to refuse
reassignment.

**The premise.** That `prior` reflects the row `ON CONFLICT` resolves against.

**Why it was false.** Every CTE in one statement shares that statement's
snapshot. `ON CONFLICT` does not — it must see rows committed after the
snapshot, or it could not enforce uniqueness, and for `DO UPDATE` it resolves
against the live row under a row lock.

So a row committed by a concurrent writer *after* the statement began is
invisible to `prior` while the conflict branch updates it.

**Reproduced deterministically [REPO].** Writer A's transaction held open until
writer B was observably blocked — by polling `pg_stat_activity` for a lock wait,
not by sleeping:

```
A (winner): {job_id: 50, inserted: True,  prior_h: None}
B (loser):  {job_id: 50, inserted: False, prior_h: None}   ← prior saw nothing
rows: 1
```

**Two failures, one unanticipated:**

| Scenario | Old behavior | Correct |
|---|---|---|
| Identical content, concurrent | `UPDATED` | `UNCHANGED` |
| Different company, concurrent | **`NoResultFound` raised** | `COMPANY_CONFLICT` |

The second was the serious one and nobody had predicted it: `prior` empty *and*
the `WHERE` skipping the update meant the `FULL OUTER JOIN` produced zero rows,
so `.one()` raised. The anomaly the design exists to *report* became an
exception.

**The correction [REPO: ADR-0015, migration 0004].** `previous_content_hash`,
captured inside the conflict branch where `jobs.*` is the live row under the
lock `ON CONFLICT` already holds. Every `SET` guarded by company equality and
the `WHERE` removed, so a row is always returned. Classification now reads from
the returned row:

```
company_id differs           → COMPANY_CONFLICT
previous_content_hash IS NULL → INSERTED
previous_content_hash == new  → UNCHANGED
otherwise                     → UPDATED
```

**Order of operations [WORKFLOW].** Eight concurrency tests were written first;
5 failed against the existing implementation. Only then was anything changed.
Afterwards: 284 passing. The tests were then deliberately re-broken to confirm
they detect the regression — 3 of 8 failed, confirming they are not vacuous.

**[LESSON].** Green tests prove the properties you thought to check under the
conditions you thought to create. The 274 were not useless — they made the fix
to the hottest path in the system safe. What they could not do is anticipate a
property nobody had articulated. That gap is human review's job, and the
question that closed it was about the substrate's semantics, not about the code.

---

## 10. Prefer a domain-controlled signal over an undocumented internal — for the right reason

**What happened [REPO: ADR-0015].** `(xmax = 0)` distinguished insert from
update. Classification: **implementation-dependent but conventional** — widely
used, stable across many major versions, but no PostgreSQL documentation defines
`xmax` on a row returned from `ON CONFLICT DO UPDATE`.

**The nuance that matters.** It was **not removed because it was ugly or
internal. It worked correctly in every test, including every race test.** The
concurrency bug forced a redesign of the classification; the redesign happened
to make `previous_content_hash IS NULL` answer the same question from data this
project defines. Losing the dependency on engine internals was a *consequence*
of fixing a real defect, not the motivation.

Alternatives evaluated and rejected [REPO: ADR-0015]: `created_at = updated_at`
fails because both default to `now()`, the *transaction* timestamp, making an
insert and a later update in the same transaction indistinguishable;
`first_seen_at = observed_at` is also true for a re-observation sharing the
instant; `RETURNING OLD.content_hash` is exact but PostgreSQL 18 only.

**[LESSON].** "Relies on internals" is a reason to *document and watch*, not an
automatic reason to rewrite. Rewriting working code to satisfy an aesthetic
preference is how regressions enter. When a real defect forces a change, take
the opportunity — but be honest in the record about which was the cause.

---

## 11. The harness improvement loop

Every significant incident followed the same shape [WORKFLOW]:

```
assumption → challenge → experiment → failing test → correction → permanent verifier
```

| Incident | Assumption | Permanent verifier |
|---|---|---|
| `observed_at` | The normalizer can supply observation history | Test asserting `first_seen_at`/`last_seen_at` absent from the model |
| Blank id | Pydantic validation is sufficient | Migration 0003 `CHECK` + 6 raw-SQL tests |
| Source consistency | Source and company agree by construction | Migration 0002 composite FK + 12 tests |
| Concurrency | `prior` reflects what `ON CONFLICT` sees | Migration 0004 + 8 race tests |

**The non-negotiable step is the failing test.** Fixing on the strength of an
argument leaves no proof the bug existed, no proof the fix addressed it, and no
barrier against return. Every correction above was preceded by a reproduction.

**[LESSON].** The loop is the methodology. Everything else is detail.

---

## 12. Human review changes shape as the harness strengthens

**What happened [WORKFLOW].** Across Steps 0–3, human attention moved away from
generated lines. Ruff, mypy and pytest were simply better at that. What humans
contributed instead:

| Review question | Outcome |
|---|---|
| "Can a normalizer know this?" | `observed_at` |
| "Is this redundancy necessary?" | Provider research, ADR-0014 |
| "Does the DB enforce what Pydantic enforces?" | Migration 0003 |
| "Can `prior` be stale?" | The concurrency incident |

None were visible in a diff. All came from questioning premises.

**[LESSON].** As verification strengthens, shift attention to: intent; missing
assumptions; architecture; boundary conditions; adversarial scenarios; and
whether the verification itself is sufficient. The last is the highest-leverage
question, and the easiest to forget when everything is green.

---

## 13. A sophisticated stack is not a sophisticated harness

**What happened [REPO].** Job Radar uses no Skills, no subagents, no hooks, no
MCP servers. There is no `.claude/` directory. The harness is: a linter, a strict
type checker, a test suite against the real database engine, a migration tool,
and written specifications and decisions.

That harness caught a snapshot-isolation bug in a concurrent upsert.

**[LESSON].** Capability without a measured problem is cost: configuration,
maintenance, and a new place for behavior to become unpredictable. Before
adopting anything, state the symptom you observed, the measurement confirming
it, and what the tool would change. If you cannot state all three, wait.

**Counterweight.** This is not an argument that the tooling is worthless — it is
an argument about ordering. `EXISTING-PROJECT.md` describes a context where
several of these become necessary rather than decorative, because discovery and
context selection are genuinely first-order problems at that scale. Job Radar
simply never reached that point.

---

## Open items carried forward

Recorded honestly rather than presented as complete.

| Item | Status |
|---|---|
| **No git commits exist in this repository** | A real gap; version control is not yet part of the harness [REPO] |
| Board-token normalization | Deferred; rules are source-specific — Greenhouse and Ashby are case-insensitive, Lever is not [REPO: ADR-0014] |
| `source_group_id` / `internal_job_id` | Recorded as a future consideration for repost tracking, not implemented [REPO: ADR-0014] |
| Exit-code semantics for partial failure | Deliberately unresolved until a scheduler consumes it [REPO: SPEC] |
| Job deactivation | Deferred; the crawl-outcome tables exist so it can be added safely [REPO: ADR-0004] |
| `updated_at` maintained by ORM, not a trigger | A raw SQL `UPDATE` does not refresh it [REPO: ARCHITECTURE] |
