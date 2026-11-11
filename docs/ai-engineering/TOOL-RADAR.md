# Tool and Concept Radar

What exists, what it costs, and what would have to be true before adopting it.

> **Nothing on this radar should be installed because it appears here.** Each
> entry names the symptom that would justify it. If you cannot point at that
> symptom in your own project, the entry is information, not a recommendation.

## Status

| Status | Meaning |
|---|---|
| **CURRENT** | In use in Job Radar, verifiable in this repository |
| **EVALUATE WHEN NEEDED** | Understood; a specific trigger would justify adoption |
| **RESEARCH** | Not sufficiently understood to evaluate; needs investigation first |
| **EXPERIMENTAL** | Frontier; not established practice for this project |

## Evidence level

| Level | Meaning |
|---|---|
| **Repository-verified** | Confirmed in this repository |
| **Vendor-documented** | From the tool's own documentation |
| **Unverified description** | Described to this project; not independently confirmed |
| **Insufficient evidence** | **Cannot be described accurately. Stated as such rather than guessed.** |

---

## CURRENT

### Claude Code
**Category** coding agent · **Evidence** Repository-verified (every step so
far, 0 through 4D-4, was produced with it)

Terminal-based coding agent: reads files, runs commands, edits code, iterates on
tool output. Its value in this project came from closing the loop itself — run
`make check`, read the failure, fix, re-run — without a human relaying results.

**Cost/risk.** Confident wrong answers on unverified premises. The concurrency
incident is the example: the implementation was plausible, passed 274 tests, and
was wrong about snapshot semantics. Mitigation is harness strength and premise-
level review, not distrust of output.

### CLAUDE.md
**Category** context · **Evidence** Repository-verified

Project instructions loaded into every session. Job Radar's holds engineering
principles, the development workflow, and the identity invariants — including
"never substitute `first_seen_at` for `source_published_at`," which is precisely
the kind of rule an agent would otherwise violate reasonably.

**Cost/risk.** It is loaded every turn, so every line is a permanent context
tax. The failure mode is accretion: rules added until the file is noise.

**Guidance.** Keep it short. Invariants and workflow, not documentation. Detail
belongs in `SPEC.md`, `ARCHITECTURE.md` and ADRs, which are read on demand.

### Deterministic gate — Ruff, mypy, pytest, PostgreSQL, Alembic
**Category** verification · **Evidence** Repository-verified

`make check`. Not usually thought of as agent tooling, but it is the highest-
leverage component in the harness: it converts agent output into a pass/fail
signal the agent can act on unaided.

**Guidance.** Build this first. Everything else on this radar assumes it exists.

### ADRs, SPEC.md, ARCHITECTURE.md
**Category** durable context · **Evidence** Repository-verified (16 ADRs)

Decision and specification records. Their agent-specific value is preserving
reasoning that code cannot carry: rejected alternatives, evidence weighed, and
what is *not* guaranteed. ADR-0014 records that provider id uniqueness scope is
undocumented across all three sources — unrecoverable from code.

**Cost/risk.** Drift. A stale ADR is worse than none because it is trusted.
`verify-change` now reviews documentation impact on every changed tree
(DOC-IMPACT, H-003).

### Skills
**Category** reusable procedure + context · **Evidence** Repository-verified
(file exists); behavior only partially exercised · **Job Radar status** one
skill, `verify-change` (H-002), extended with a documentation impact review
(H-003) and evidence-capture rules (H-004)

A packaged procedure with its context, loaded on demand when a task matches.
Progressive disclosure: only the name and description are loaded every
session. The body costs nothing until the skill is invoked.

**Trigger.** You have explained the same multi-step procedure across three or
more sessions.

**Symptom that justified it.** An orientation audit could only run lint, type
checks and 169 unit tests. The 184 integration tests were collected but not
run, because PostgreSQL was down. Nothing in the repository defined how to
report that, so "unit tests green" could be mistaken for "verified". The
integration suite also creates databases and drops schemas, which no written
procedure warned about.

**What it does.**
- Classifies the diff conservatively.
- Runs the existing Makefile gates one by one.
- Refuses integration tests against an unverified database target, and asks
  before every integration run.
- Reviews the documentation relevant to the change (DOC-IMPACT): which
  documents were read, and whether each is updated, still accurate, newly
  contradicted, or carries older debt. A contradiction the change introduces
  fails the gate; older debt is reported without failing it. The verdicts are
  agent judgment, not a deterministic check.
- Runs every gate in the foreground, saving its output outside the repository
  and capturing its exit code immediately (H-004). PASS needs that exit code
  and the tool's own completion output; a check that ran without them is
  UNVERIFIED, never inferred to pass.
- Reports PASS/FAIL/UNVERIFIED/BLOCKED/SKIPPED per check and
  PASS/FAIL/BLOCKED/PARTIAL overall.

It is followed by the agent, not enforced by the harness.

**Cost/risk.** Skills that overlap or contradict each other; maintenance as the
procedure drifts from reality. `verify-change` hard-codes test database names
and destructive operations, so it re-reads them from `tests/` on each run
rather than trusting its own text.

**Symptom that justified DOC-IMPACT (H-003).** Nothing in the gate looked at
documentation. An audit after Step 4D-3 found documents contradicting the code
they describe, none of them caught by any check: SPEC, ARCHITECTURE and
ADR-0011 said an unchanged job's upsert writes only `last_seen_at`; ADR-0006
called two nullable fields required; README quoted test counts several steps
old.

**Symptom that justified evidence capture (H-004).** During Step 4D-4 an
integration run was approved for one execution, but the agent ran it as a
background command that discarded its output and exit code. The run used up
the approval, yet its result could not be established. The skill had no
status for that case, so it could only be misreported as passed, blocked or
skipped. H-004 adds UNVERIFIED (ran, or may have run, without the evidence a
PASS needs), a foreground capture pattern, per-tool pass evidence, a list of
evidence-masking patterns, and the rule that a lost integration run consumes
its approval: it is reported UNVERIFIED, overall PARTIAL, and not rerun
without fresh approval. It is a procedural rule, not a new execution
framework, and like the rest of the skill it is followed, not enforced.

**Not yet verified (radar step 5).** Whether it solved the measured problem
has not been shown. Since H-002 it has been exercised in working sessions, but
not in any way recorded in this repository: with PostgreSQL available and each
integration run approved separately, and with integration omitted (reported
PARTIAL). A failing check reported through the skill has not been exercised.
DOC-IMPACT has not been exercised beyond the change that introduced it.
H-004's capture pattern was shown to preserve a nonzero exit code with a
harmless command; it has not yet been exercised on a real lost-evidence run.

---

## EVALUATE WHEN NEEDED

### Subagents
**Category** parallelism / context isolation · **Evidence** Vendor-documented ·
**Job Radar status** not used

Separate agent instances with their own context, returning a result. Two real
problems they solve: broad search that would otherwise flood the main context,
and genuinely independent work that can run concurrently.

**Trigger.** Discovery reading consumes context needed for implementation, or
several independent investigations would otherwise run serially.

**Cost/risk.** Coordination overhead; loss of shared context; harder to follow
what happened. **Introduce for a measured problem, never because multi-agent
sounds advanced.**

**Why not yet.** Job Radar is ~18 production modules. Nothing approached context
pressure. The brownfield path is where this becomes plausible first — see
`EXISTING-PROJECT.md` Phase 1.

### Hooks
**Category** deterministic automation · **Evidence** Vendor-documented ·
**Job Radar status** not used

Actions the harness runs on events — before a tool call, after an edit. Executed
by the harness rather than chosen by the model, so unlike an instruction they
are reliable.

**Trigger.** A step is repeatedly forgotten, or must be guaranteed rather than
requested.

**Cost/risk.** Invisible behavior. A hook that reformats or blocks can confuse
both the agent and the human when it fires unexpectedly.

**Why not yet.** `make check` was run explicitly at every gate. A hook would
have automated a step that was not being skipped.

### MCP (Model Context Protocol)
**Category** tool/data integration · **Evidence** Vendor-documented ·
**Job Radar status** not used

Standard protocol for exposing external systems — databases, issue trackers,
internal services — to an agent.

**Trigger.** The agent needs data it cannot reach through the filesystem or
shell, repeatedly.

**Cost/risk.** Each server is a dependency, a permission surface, and context
cost for its tool definitions. Servers are third-party code with access to the
session.

**Why not yet.** `psql` through Docker Compose and `curl` covered every need,
including the live provider research.

### Git worktrees
**Category** isolation · **Evidence** Vendor-documented ·
**Job Radar status** not used

Multiple working directories from one repository, so parallel work does not
collide.

**Trigger.** Concurrent agent work on the same repository.

**Note [REPO].** When this entry was first written the repository had no
commits, and that was recorded as a genuine gap in the harness. Ordinary
version control is now in use, so the remaining trigger is concurrent work.

### Agent observability
**Category** measurement · **Evidence** Vendor-documented (varies by tool) ·
**Job Radar status** not used

Visibility into tool calls, token spend, and where time goes.

**Trigger.** You suspect a context or cost problem. **Prerequisite for the
context-optimization entries below** — optimizing an unmeasured problem is
guesswork.

---

## RESEARCH

> The entries below were named as worth investigating. **This project has no
> repository evidence for any of them, and several cannot be described
> accurately from available knowledge.** Where that is the case it is stated
> plainly rather than filled in. Verify against primary sources before acting.

### Graphify
**Category** codebase indexing / structural context · **Evidence** Insufficient

**Cannot describe this tool accurately.** No repository evidence, and no
reliable independent knowledge of its design or current state. The description
this project holds is: tooling for codebase structure discovery, intended to
reduce repeated exploration cost.

**The underlying problem is real** — in large or multi-repository systems,
re-deriving structure every session is expensive — and it is the category to
evaluate when brownfield discovery becomes the bottleneck. Whether this
particular tool addresses it well is unknown.

**Trigger.** Structural exploration measurably dominates session cost.
**Why not here.** Job Radar's structure fits in one `find` invocation.

### Caveman
**Category** context / token efficiency · **Evidence** Insufficient

**Cannot describe this tool accurately.** Held description: patterns for
reducing context and token consumption.

**Trigger.** A *measured* token or context problem — which requires
observability first. Adopting an efficiency tool without measurement optimizes
an unknown.

### Codeman (referred to as Claudeman previously)
**Category** agent/session orchestration · **Evidence** Insufficient

**Cannot describe this tool accurately, including whether the naming above is
current.** Held description: orchestration of agents or sessions.

**Trigger.** Multiple persistent agents or repositories create genuine
coordination problems — plausible on the brownfield path, absent here.

### Ralph / Ralph loops
**Category** autonomous iteration · **Evidence** Insufficient for specifics

A pattern rather than a product as this project understands it: running an agent
repeatedly against a goal until a completion condition is met, rather than
one task per human turn. Details of any specific implementation are not
independently verified here.

**The critical property** is that an autonomous loop is exactly as safe as the
gates bounding it. With strong deterministic gates, iteration converges; with
weak gates, it amplifies errors and produces plausible, unverified volume.

**Trigger.** Deterministic gates strong enough that a wrong iteration is caught
automatically, plus a bounded blast radius.
**Status here.** Job Radar's gate is strong for its size, but every step was
human-approved. Autonomy was deliberately not pushed.

### OpenSpec
**Category** spec-driven development tooling · **Evidence** Insufficient

**Cannot describe this tool accurately.** Held description: tooling to structure
specification-driven work with agents.

**Relevant regardless of the tool:** Job Radar practised spec-driven development
with plain Markdown — `SPEC.md`, `ARCHITECTURE.md`, ADRs — and that was
sufficient through Step 3 [REPO]. Evaluate tooling if specifications become
numerous enough that plain files stop scaling.

### LIDR Specboot
**Category** unknown · **Evidence** Insufficient

**Cannot describe this. No reliable knowledge, no repository evidence.**
Recorded so the name is not lost, explicitly without a description.

---

## EXPERIMENTAL

### Self-improving harness
**Category** frontier · **Evidence** None · **Status** EXPERIMENTAL

The idea: the agent observes its own failures and proposes new checks,
tightening the harness without human authorship.

Job Radar did something adjacent but importantly different [WORKFLOW]: each
incident produced new permanent tests, but a **human questioned the premise**
each time and approved each promotion. The hypothesis originated with the human;
the agent supplied experiment, reproduction and implementation.

**Why this is frontier.** The failure mode is circular: a harness that generates
its own criteria can drift toward checks that are easy to satisfy. The
concurrency incident is the counter-example — it required questioning a premise
that *all existing evidence supported*. Nothing in this project's experience
suggests that comes for free.

**If explored:** generated checks should be reviewed before promotion, and each
should be proven capable of failing before it is trusted.

### Fully autonomous multi-repository change
**Category** frontier · **Evidence** None · **Status** EXPERIMENTAL

Sketched in `EXISTING-PROJECT.md`. Requires per-repository gates, executable
contracts, cross-repository verification, and reliable impact analysis — none
of which this project has built.

---

## How to use this radar

```
1. Name the symptom you have actually observed.
2. Measure it. If you cannot, that is the first task.
3. Find the entry whose trigger matches.
4. If the evidence level is "Insufficient", investigate before adopting.
5. Adopt one thing. Verify it solved the measured problem.
6. If it did not, remove it.
```

Step 6 is the one that gets skipped, and it is why tooling accumulates.

**Job Radar's current position:** every CURRENT entry, nothing else. Steps 0–3
produced 284 tests, 4 migrations and 15 ADRs on that basis, and the harness
caught a snapshot-isolation bug that the tooling on this radar would not have
found. Sophistication of stack is not sophistication of harness.
