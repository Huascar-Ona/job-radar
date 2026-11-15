# H2 Experiment 001: implementing 4H under a fixed contract

- Status: **approved** (maintainer, 2026-11-14)
- Version: 1.0
- Subject: `docs/contracts/4H-crawl-run-reporting.md`, version 1.0, SHA-256
  `788ed0d55adb14b9a6cc1c89aeb20fae22ed988df0d477b9cf260d9c60dcdcea`

## Question

Can an AI coding agent, given only the approved contract and the repository,
implement 4H so that it passes the project's gates and an acceptance suite it
has not seen, and how much human correction does it need? Token use and time
are recorded for context; they are not a quality score.

## Fixed inputs (recorded before implementation)

| Item | How it is fixed |
|---|---|
| Baseline commit | The commit `docs(harness): finalize 4H contract and H2 evaluation protocol`, which contains the approved contract, this protocol and the H1 closure (`git log --format=%H -F --grep='docs(harness): finalize 4H contract and H2 evaluation protocol'`). Every comparison starts from it |
| Contract | `sha256sum docs/contracts/4H-crawl-run-reporting.md` at approval; the version line in the file |
| Implementation prompt | One fixed text, stored outside the repository, recorded by SHA-256; it points at the contract and the project's own rules and says nothing about the held-out tests |
| Implementing agent | Claude Code, the client version from the transcript, the model id (`claude-opus-5-5` so far) and effort level, a fresh session started for this experiment only |
| Held-out suite | SHA-256 of each held-out file, recorded before the implementer starts |
| H1 attribution | A new local session alias and a segment `4H` covering the implementer's turns, `reviewed` once checked |

## Roles and isolation

Three roles, never the same agent session:

1. **Coordinator** (this session, or the maintainer): drafts the contract and
   this protocol, records the fixed inputs, runs the evaluation steps. It
   does not write the held-out tests, so their content never enters its
   context, and it does not implement.
2. **Evaluator**: a separate Claude Code session (approved 2026-11-14),
   started with only the evaluator handoff and the approved contract. It
   writes the held-out tests in its own directory, outside the repository
   and outside the implementer's worktree, and hands over only their file
   names and hashes until evaluation. The maintainer reviews them.
3. **Implementer**: a fresh Claude Code session in its own Git worktree at the
   baseline commit, given only the fixed prompt.

What isolation can and cannot be guaranteed:

- A test file in another directory is **not** hidden: an agent running as the
  same user can read any file the user can, and a session that wrote the
  tests remembers them. Same-session or same-directory evaluation is
  therefore not independent, and this protocol does not claim it is.
- Defence in depth for the implementer (procedural, not a guarantee): the
  held-out files live outside its worktree; its worktree's
  `.claude/settings.local.json` denies `Read` and `Bash` access to that path;
  where the Claude Code sandbox is available, its filesystem read deny list
  includes the path.
- Detection after the fact: before evaluation, the coordinator searches the
  implementer's transcript locally for the held-out directory's path and the
  held-out files' names, printing only whether any occurred. A hit makes the
  run **contaminated**: its results are recorded but not counted as
  independent.
- The evaluator session is itself an agent running as the same user, so
  the implementer could in principle read its directory or transcript; the
  deny rules above cover the held-out directory, and the contamination
  check also searches for the evaluator's directory path.

## Definitions

- **First implementation attempt**: everything the implementer does from the
  fixed prompt until it first reports the implementation complete, with no
  human message in between other than permission approvals and the
  authorizations the project's rules require (an integration run).
- **First verification run**: on the first attempt's tree (its commit or
  tree hash recorded): the verify-change gates (lint, format, typecheck, unit,
  DOC-IMPACT, `git diff --check`, and one authorized `make test-integration`),
  then the held-out suite, each once, with captured evidence.
- **Correction**: any change to code, tests or documentation after the first
  verification run, made in response to a failure or to human feedback.
- **Retry**: a re-run of a gate or of the held-out suite after a correction
  (H1 "failures re-run"). Routine re-runs with no preceding failure are
  recorded as later runs, not retries.
- **Human intervention**: a maintainer message after the fixed prompt that
  changes scope or the contract (`scope_change`, `contract_amendment`),
  points out a mistake (`correction`), rejects a proposal
  (`rejected_proposal`), or answers a question the agent had to ask
  (`decision_request`). Permission approvals and required authorizations are
  recorded separately and are not interventions.
- **Regression**: a test that passes at the baseline commit and fails on an
  evaluated tree; or a defect in 4H found after final acceptance and before
  the next product milestone closes (an escaped defect).
- **Final acceptance**: every gate passes, the held-out suite passes, the
  maintainer reviews the change, and the commit is authorized.

## Verification commands

```
.venv/bin/ruff check .
.venv/bin/ruff format --check .
make typecheck
make test-unit
make test-integration                      # one authorized run per attempt
git diff --check
.venv/bin/pytest -m integration <held-out files copied into a scratch copy of the implementer's tree>
```

The held-out files are copied into a disposable copy of the evaluated tree
(they need the project's fixtures), run once, and removed; they are never
committed to the implementer's worktree before final acceptance.

## Metrics and record

One record, `docs/metrics/experiments/001-4H.json`, codes and counts only:
baseline commit, contract version and hash, prompt hash, held-out file
hashes, agent client version, model, effort; first-attempt gate results;
held-out passed/total on the first and final run; corrections; failures
re-run; later runs; interventions by code with turn numbers; permission and
integration authorizations; regressions; contamination check result;
verdict (`accepted`, `accepted_after_corrections`, `not_accepted`,
`contaminated`). Time and tokens come from H1 for the `4H` segment and are
reported beside the quality figures, never combined with them.

- **First-pass success**: every gate and every held-out test passes on the
  first verification run, with no `correction` or `scope_change` before it.

## Decisions (approved by the maintainer, 2026-11-14)

- Held-out acceptance tests are written by a separate evaluator session,
  never by the coordinator or the implementer.
- Escaped defects are tracked until the next product milestone closes.
- The contract decisions are listed in the contract itself.

## Execution plan

1. **Contract approval**: the maintainer settles the open decisions and
   approves the contract; the coordinator records its hash.
2. **Independent evaluation preparation**: the evaluator writes the held-out
   suite from the approved contract outside the implementer's reach; the
   coordinator records the hashes. The baseline commit (contract, protocol,
   H1 closure) is created on authorization.
3. **Implementation authorization**: the maintainer authorizes the run; the
   coordinator creates the implementer's worktree at the baseline and its
   deny rules, and records the fixed prompt's hash.
4. **Agent implementation**: a fresh implementer session receives only the
   fixed prompt and works to its first completion report.
5. **Normal verification**: verify-change on that tree, including one
   authorized integration run.
6. **Held-out evaluation**: contamination check, then the held-out suite once
   on a scratch copy of the tree.
7. **Feedback and correction**: failures and review findings go back to the
   implementer as `correction` interventions; each fix is re-verified
   (retries), with the held-out suite re-run only on the corrected tree.
8. **Final acceptance and metrics**: maintainer review, commit, H1 segment
   marked `reviewed`, the experiment record and the 4H outcomes written.
