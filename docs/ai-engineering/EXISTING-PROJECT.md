# Brownfield: Introducing AI-Native Engineering into an Existing System

For large, long-lived, often multi-repository systems.

> **Status of this document.** Job Radar is greenfield. **Nothing here has been
> validated on a brownfield system by this project.** It is a design derived
> from the Job Radar methodology plus the structural differences of existing
> systems. Treat it as a proposal to test, not a report of results.
> Where a technique *was* validated on Job Radar, it is marked [REPO].

---

## The direction reverses

```
GREENFIELD                        BROWNFIELD

need                              existing code
  ↓                                 ↓
spec                              discovery
  ↓                                 ↓
architecture                      reverse engineering
  ↓                                 ↓
harness                           system map
  ↓                                 ↓
implementation                    contracts / invariants
                                    ↓
                                  harness
                                    ↓
                                  safer agent autonomy
```

Greenfield decides intent and derives structure. Brownfield recovers intent from
structure — and the structure is authoritative whether or not anyone still
agrees with it.

Three consequences:

1. **You cannot choose the invariants.** You discover which ones the system
   already relies on, including accidental ones.
2. **Behavior is the specification,** including bugs other systems now depend on.
3. **The blast radius is real.** A greenfield mistake costs a re-run. A
   brownfield mistake costs an incident.

---

## A motivating ecosystem

A hypothetical multi-repository system of the kind this path targets:

```
  ┌──────────────┐     ┌───────────────┐
  │  embedded    │     │   dashboard   │
  │   widget     │     │   frontend    │
  └──────┬───────┘     └───────┬───────┘
         │                     │
         └────────┬────────────┘
                  ▼
         ┌────────────────────┐        ┌──────────────────┐
         │  core backend API  │◄──────►│  third-party     │
         └─────┬───────┬──────┘        │  integrations    │
               │       │               └──────────────────┘
      ┌────────▼──┐ ┌──▼─────────────┐
      │ inventory │ │   finance /    │
      │ ingestion │ │  accounting    │
      │  / admin  │ │                │
      └───────────┘ └────────────────┘
```

**Every arrow in that diagram is a hypothesis.** Direction, protocol,
synchronicity and coupling must be recovered from evidence — code, configuration,
logs, deploys — not assumed from the shape. The most consequential unknowns are
usually:

- Which repository *owns* a given concept versus merely reads it
- Whether integration is synchronous, event-driven, or a batch job nobody recalls
- Where money-affecting state transitions actually commit
- Which "internal" APIs have external consumers

Record each as **known / inferred / unknown**. The discipline that kept Job
Radar honest about provider APIs applies with more force here: the cost of a
wrong assumption is higher. [LESSON]

---

## Why not "put every repository in context"

The reflex is to load everything. It fails on four counts:

1. **Size.** Context windows are finite; large systems are not.
2. **Signal dilution.** Irrelevant context measurably degrades attention on
   relevant context. More is not more.
3. **Staleness.** A dump is a snapshot; repositories move.
4. **Cost.** Every token, every turn, forever.

The alternative:

```
system knowledge          durable, curated, small, human-reviewed
    +
search / retrieval        find the relevant 200 lines on demand
    +
progressive context       load when the task needs it
    +
repo-local harnesses      each repository verifies itself
    +
cross-system verification contract tests at the seams
```

Job Radar validates the fourth and fifth elements at small scale [REPO]. The
first three are [RADAR] for this project.

---

## Phase 1 — Discovery

**Objective.** Establish what exists, from evidence.

**Per repository, recover:**

| Question | Evidence |
|---|---|
| What is this responsible for? | README, entry points, route/handler tables |
| What does it own? | schema ownership, write paths |
| What does it call? | HTTP clients, queue producers, DB connections |
| What calls it? | routes, consumers, webhooks — hardest, often incomplete |
| How is it built, tested, run? | CI config, Makefile, package manifests |
| What is the local verification? | test commands and what they actually cover |

**Artifact.** One page per repository. Short, current, explicitly marked where
uncertain.

**Human decides.** What is authoritative.
**Agent can.** Read, summarize, map call sites, tabulate dependencies. This is
mechanical and high-volume — the natural first use of subagents, if context
pressure is measured [RADAR].

**Gate.** Every repository has a one-page description whose uncertain claims are
labeled.

**Failure mode.** Documenting intended architecture instead of actual. If the
document and the code disagree, the code is correct.

---

## Phase 2 — System map

**Objective.** Make cross-repository structure explicit.

**Contents:**

- Repository responsibilities and owners
- Ownership boundaries: which system is the source of truth for each concept
- Contracts: API endpoints, event schemas, message formats, shared tables
- Dependency direction, and whether each is sync or async
- Shared concepts and where their definitions diverge
- Integration boundaries and failure modes

**The ownership question is the load-bearing one.** "Which repository may write
this?" determines where a change is safe. Shared concepts that mean subtly
different things in different repositories are where cross-system bugs live.

**Gate.** For any concept, you can name its owner or explicitly record that
ownership is unclear.

**Failure mode.** A diagram that is never updated. Prefer a small map that is
current over a complete one that is not.

---

## Phase 3 — Contracts and invariants

**Objective.** Write down what must hold *between* systems, then make it
executable.

**Ask.** What does each consumer rely on? Which fields are load-bearing? What
ordering, idempotency and retry semantics are assumed? What happens on partial
failure across a boundary?

**Contract tests** are the brownfield analogue of Job Radar's database
constraints: they hold regardless of which code path runs, and they fail loudly
at the seam rather than silently downstream.

Job Radar's lesson transfers directly [REPO: ADR-0010]: verify against the real
substrate. A contract test against a hand-written mock proves the mock matches
the test.

**Gate.** Each identified contract is executable, or explicitly recorded as
unverified.

**Failure mode.** Documenting contracts without executing them. Prose contracts
drift the same way prose boundaries do.

---

## Phase 4 — Repository-local harnesses

**Objective.** Each repository independently verifiable by one command.

This is Job Radar Stage 7, applied per repository — but with a backlog.
Strictness cannot be switched on globally. Realistic sequencing:

1. Make the existing suite runnable by one documented command
2. Add formatting/lint with existing violations grandfathered
3. Add type checking in non-strict mode; tighten per module
4. **Add strict checking for new and changed code only**
5. Raise the floor as the backlog drains

Step 4 is the one that matters: agent-generated code lands under the strict
regime from the start, so the harness improves as work proceeds rather than
requiring a project to fix it. [LESSON]

**Gate.** Every repository an agent may touch has a one-command local gate.

**Failure mode.** Blocking all agent work behind a full-repository cleanup.

---

## Phase 5 — Cross-repository verification

**Objective.** Catch what per-repository gates cannot: contract breakage.

Needs a place to run contract tests across services, and a policy for what
blocks a merge when a consumer is in a different repository. This is genuine
infrastructure work and is where most of the cost of this path sits.

---

## Investigation before implementation

The practice that most reduces brownfield risk, and the one most often skipped.

**Before any cross-repository change, produce an explicit impact analysis:**

```
AFFECTED         — changes required. Evidence: file, line, why.
NOT AFFECTED     — considered and ruled out. Evidence for the ruling.
UNCERTAIN        — cannot determine from available evidence. What would resolve it.
```

The **UNCERTAIN** bucket is the point. An agent asked "which repositories does
this affect?" will produce a confident list. An agent asked to populate three
buckets *with evidence* produces a list plus an honest account of what it could
not determine — which is what a reviewer actually needs.

This mirrors the discipline that worked on Job Radar's provider research:
documented / observed / assumption [REPO: ADR-0014]. Same idea, higher stakes.
[LESSON]

**Gate.** No implementation begins while a load-bearing item sits in UNCERTAIN.

---

## A possible future workflow

**Not implemented. Not validated. A hypothesis to test incrementally.**
[RADAR]

```
issue
  ↓
investigation agent ──────► reads repos, gathers evidence
  ↓
impact map ───────────────► AFFECTED / NOT AFFECTED / UNCERTAIN
  ↓
human resolves UNCERTAIN ◄── checkpoint
  ↓
implementation plan ──────► per-repository, ordered by dependency
  ↓
human approves plan ◄────── checkpoint
  ↓
implementation ───────────► per repository
  ↓
repo-local verification ──► each repository's own gate
  ↓
cross-repo contract verification
  ↓
review agent ─────────────► adversarial pass
  ↓
human approval ◄─────────── checkpoint
```

Three human checkpoints, placed where a wrong answer is cheapest to correct:
after investigation, after planning, before merge.

**Do not build this first.** Each element earns its place only after the
preceding phase is real. An orchestration pipeline over repositories without
local gates automates the production of unverified changes.

Sensible order: discovery → system map → local harnesses → contract tests →
impact analysis as a *manual* discipline → only then automate the parts that
have proven repetitive.

---

## What transfers from Job Radar, and what does not

**Transfers:**

| Lesson | Brownfield form |
|---|---|
| Executable feedback before autonomy | Per-repository gates before cross-repo agent work |
| Verify against the real substrate | Contract tests against real services |
| Documented vs observed vs assumed | Known / inferred / unknown in the system map |
| Tests must be capable of failing | Contract tests must fail on real breakage |
| Promote invariants to the strongest layer | Schema and API-level enforcement over convention |
| Question premises, not lines | Question ownership and coupling assumptions |
| Add tooling against measured problems | Same, with more temptation to skip it |

**Does not transfer:**

- Choosing the stack. It is chosen.
- Inventing invariants. You discover them.
- Cheap mistakes. They are not cheap.
- Small context. Selection and retrieval become first-order problems, which is
  the one area where brownfield genuinely needs tooling Job Radar never did.
