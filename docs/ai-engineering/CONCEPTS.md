# Concepts

Operational definitions as used in this project. Several of these terms are
emerging rather than settled; where that is the case it is said so, and the
definition given is the one this project works with — not an appeal to a
standard that does not yet exist.

Labels: **[REPO]** verifiable here · **[WORKFLOW]** observed · **[LESSON]**
generalization · **[RADAR]** not in use · **[EXPERIMENTAL]** frontier.

---

## The layer model

```
    model            weights. probabilistic. you do not control it.
      │
    agent            loop: read context → choose tool → act → observe
      │
    tools            file edit, shell, search, network, MCP servers
      │
    context          what the model can see right now
      │
    repository       code, tests, config, docs — the durable substrate
      │
    verification     lint · types · tests · constraints · migrations
```

Each layer constrains the one above. You cannot make the model deterministic.
You *can* make the repository and verification layers deterministic, and that is
where correctness is manufactured. [LESSON]

**Model** — the weights. Interchangeable, improving, outside your control.

**Coding agent** — the loop wrapped around a model: read context, select a tool,
act, observe the result, repeat. Claude Code is a coding agent. The agent, not
the model, decides what to read and what to run.

**Agent harness** — the surrounding machinery that shapes agent behavior:
permissions, tool availability, automation, session configuration. Largely
supplied by the tool vendor.

**Project / repository harness** — what *you* build: the checks, constraints,
specifications and conventions that live in the repository and apply to any
agent, any model, any session. **This is the durable asset.** Job Radar's
harness is `make check`, PostgreSQL's constraints, the ADR set, and
`CLAUDE.md` [REPO].

**Harness Engineering** — treating that project harness as a first-class
engineering artifact, designed and evolved deliberately, rather than as
incidental tooling. The load-bearing question is not "does the agent write good
code" but "what would have to be true for a mistake of this class to survive to
production." [LESSON]

**Context Engineering** — deciding what the model sees, when. Not "give it
everything"; context is finite, and irrelevant context measurably degrades
attention on the relevant parts. The craft is selection and timing.

**Spec-Driven Development** — the specification is written, reviewed and agreed
*before* implementation, and remains the reference during it. With agents this
matters more than with humans: an agent will confidently implement a plausible
reading of an ambiguous requirement, and the result will look finished.
[LESSON]

---

## Verification vocabulary

**Deterministic verification** — a check that returns the same verdict every
time for the same input, with no judgment involved. `mypy` either passes or
does not. Contrast with review, which is probabilistic and expensive.

**Invariant** — a property that must always hold. "A job is identified by
`(source, source_job_id)`." Prose invariants are aspirations.

**Executable invariant** — the same property, enforced by something that runs:

| Invariant | Executable form | Where |
|---|---|---|
| Job identity is unique | `UNIQUE (source, source_job_id)` | PostgreSQL [REPO] |
| A job's source matches its company's | composite FK `(company_id, source)` | PostgreSQL [REPO: ADR-0013] |
| `source_job_id` is never blank | `CHECK (source_job_id ~ '\S')` | PostgreSQL [REPO: migration 0003] |
| Domain never imports Greenhouse | AST scan over the package | pytest [REPO] |
| Repositories never commit | source scan for `.commit()` | pytest [REPO] |
| Counters are non-negative | `CHECK (... >= 0)` | PostgreSQL [REPO] |

Note the last two rows of a different kind: checks that constrain *how the code
is written*, not what it computes. These are cheap and unusually effective with
agents, because an agent will otherwise satisfy a prose rule until it is
inconvenient. [LESSON]

**Feedback loop** — anything that turns an action into a signal the agent can
act on without a human. `make check` is the primary loop here [REPO].

**Human / design review** — the irreducible part. Not line-reading; premise-
questioning. See `GUIDE.md`.

**Agent autonomy** — how much the agent does between human checkpoints. A
function of harness strength, not of trust. Job Radar's autonomy grew because
verification grew [WORKFLOW].

---

## Agent capability vocabulary

**None of the following are used in Job Radar.** There is no `.claude/`
directory in this repository [REPO]. They are defined so the distinctions are
clear when a real need appears — see `TOOL-RADAR.md`.

**`CLAUDE.md`** — a context file, loaded into the session. **It is not an
agent.** It holds project instructions, invariants and conventions. Job Radar's
[REPO] states engineering principles, the workflow to follow, and the identity
invariants. Being always-loaded, it should be short: everything in it costs
context on every turn.

**Skill** — a packaged, reusable procedure plus its context, loaded *on demand*
when the task matches. **A Skill is not another agent**; it is instructions and
resources that enter the current agent's context when relevant. The point is
progressive disclosure. [RADAR]

**Subagent** — a separate agent instance with its own context window, given a
task, returning a result. Useful when work would otherwise flood the main
context (broad search) or can run in parallel. The cost is coordination and
loss of shared context. [RADAR]

**MCP (Model Context Protocol)** — a standard for exposing external tools and
data to an agent: a database, an issue tracker, an internal service. Extends
reach beyond the filesystem and shell. [RADAR]

**Hook** — a deterministic action the *harness* runs on an event: before a tool
call, after an edit, on session end. Hooks are executed by the harness, not by
the model, which makes them reliable in a way that an instruction in `CLAUDE.md`
is not. "Always run the formatter after editing" is a hook, not a request.
[RADAR]

**Orchestration** — coordinating multiple agents or sessions across tasks or
repositories. A solution to a scale problem; creates its own problems. [RADAR]

**Codebase memory / structural context** — durable representation of a codebase
(index, graph, summaries) so an agent can navigate without re-reading. Valuable
in proportion to codebase size; near-worthless at Job Radar's size. [RADAR]

**Agent observability** — visibility into what the agent did: tools called,
tokens spent, where time went. Prerequisite for optimizing anything. You cannot
justify a context optimization you have not measured. [RADAR]

**Autonomous loop** — the agent runs repeatedly against a goal without
per-iteration human input. Only as safe as the gates that bound it: an
autonomous loop with weak verification amplifies errors instead of work.
[EXPERIMENTAL]

**Progressive disclosure** — the organizing principle behind Skills, subagents
and retrieval. Give the agent what it needs *when it needs it* rather than
injecting everything permanently. Applies to `CLAUDE.md` too: the reflex to add
"one more rule" is how a context file becomes noise. [LESSON]

---

## The Job Radar stack and what each part contributes

Conceptual analogies for readers coming from PHP/Laravel or Ruby/Rails. **These
are approximate** — they map roles, not behavior, and each differs in important
ways.

| Laravel / Rails | Job Radar | Analogy holds for | Breaks down because |
|---|---|---|---|
| Eloquent / ActiveRecord | SQLAlchemy ORM | mapping rows to objects | SQLAlchemy is Data Mapper, not Active Record: models don't know how to save themselves; a `Session` does |
| Laravel migrations | Alembic | versioned schema change | Alembic autogenerates by *diffing models against the live database* |
| PDO / pg driver | psycopg | driver | — |
| Form Request validation | Pydantic | validating untrusted input | Pydantic models are the runtime type system, used throughout, not just at HTTP edges |
| PHPUnit / Pest | pytest | test runner | fixtures compose by dependency injection |
| PHPStan / TS | mypy | static checking | `strict = true` is closer to TS `strict` than to low-level PHPStan |
| ESLint / Prettier | Ruff | lint + format | one tool, both jobs, very fast |

### Harness contribution of each component

| Component | What it contributes | Evidence |
|---|---|---|
| **Python 3.13** | Modern typing (PEP 695 generics used in the race harness) | [REPO] |
| **Pydantic v2** | Rejects bad data at the boundary. Rejected naive datetimes before they could reach the database | [REPO: `domain/job.py`] |
| **SQLAlchemy 2** | Typed data access; Core for explicit SQL where it matters | [REPO] |
| **Alembic** | Schema is versioned and reversible; `alembic check` proves models and migrations agree | [REPO: `test_migrations.py`] |
| **psycopg 3** | Returns timezone-aware datetimes; exposes real `IntegrityError` | [REPO] |
| **PostgreSQL** | **The strongest verifier in the stack.** Constraints hold regardless of which code path writes | [REPO: ADR-0010] |
| **pytest** | Executable invariants; parametrization makes exhaustive cases cheap | [REPO: 284 tests] |
| **mypy strict** | Catches whole error classes before runtime; forces honesty about `None` | [REPO] |
| **Ruff** | Style and a broad correctness rule set (`S`, `B`, `DTZ`, `PT`, `ARG`) | [REPO: `pyproject.toml`] |
| **Docker Compose** | Real PostgreSQL on every machine; no SQLite substitution | [REPO] |
| **Git** | Version control. **Note: this repository currently has no commits** — a real gap in the harness | [REPO] |
| **SPEC.md** | Defines expected behavior; the reference during implementation | [REPO] |
| **ARCHITECTURE.md** | Defines boundaries and transaction ownership | [REPO] |
| **ADRs (15)** | Preserve *why*. Without them, a later agent re-litigates settled decisions | [REPO] |

The ADRs deserve emphasis. An agent reading only code sees the decision but not
the alternatives rejected or the evidence weighed. ADR-0014 records that
provider id uniqueness scope is **undocumented** for all three sources — a fact
no amount of code-reading recovers. [LESSON]

---

## SQLAlchemy ORM vs Core

SQLAlchemy offers two layers over the same engine and transaction machinery.

**ORM** — declarative models, identity map, unit of work. Job Radar uses it for
**schema definition**: `models.py` declares tables, constraints and indexes, and
Alembic autogenerates migrations by diffing those models against the database
[REPO].

**Core** — explicit SQL construction and execution, no object identity.

Job Radar's repositories issue **explicit SQL through Core** rather than ORM
persistence [REPO: `job_repository.py`]. The reason is specific: the upsert
depends on `INSERT ... ON CONFLICT ... DO UPDATE` with per-column `CASE` guards
and a `RETURNING` clause. That statement *is* the design — it is where
idempotency, the company-conflict anomaly, and the payload write-guard all live.
Expressing it through ORM persistence would obscure exactly the part that
matters.

**Pattern:** use the ORM where its structure pays (schema, relationships,
ordinary reads); drop to Core where the SQL carries the invariant. Do not treat
the choice as ideological. [LESSON]

---

## Why Alembic matters more with agents

An agent can change a model in seconds. Without migrations, model and database
diverge silently and the divergence is discovered in production.

`alembic check` is the executable form: it fails if models and migrations
disagree [REPO]. It runs in the gate, and there is a companion test that
deliberately drops a table to prove `check` can actually fail — because a
verifier that cannot fail proves nothing [REPO: `test_migrations.py`].

---

## Why not Django

Django bundles ORM, migrations, admin, auth, and HTTP into one framework with
strong conventions. Job Radar's first milestone has **no HTTP surface at all** —
it is a CLI/scheduler ingestion pipeline [REPO: `docs/ARCHITECTURE.md`]. Adopting
Django would mean accepting its ORM and migration system to get parts not yet
needed, and Django's ORM makes the Core-level SQL the upsert depends on more
awkward.

This is not a claim that Django is worse. It is a claim that for *this* shape of
system — no UI, no auth, one critical SQL statement — the assembled stack fits
better. A job board with an admin interface would weigh differently.

## Why FastAPI can be added later without disruption

Because the domain does not know that HTTP exists.

`NormalizedJob` is a Pydantic model with no transport or persistence concerns,
enforced by an import test that scans the domain package's AST [REPO:
`test_domain_boundary.py`]. Repositories take a `Session` and never commit;
transaction ownership sits above them [REPO: `docs/ARCHITECTURE.md`].

Adding FastAPI means adding an HTTP layer that *calls into* this, and its
request/response models can reuse the same Pydantic types. Nothing in the
domain or persistence layers needs to change [LESSON]. Deferring the framework
until the need is real is only cheap **because** the boundary was made explicit
and mechanically enforced first — otherwise a framework tends to spread
through the codebase from the edge inward.
