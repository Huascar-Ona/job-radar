---
name: project-handoff
description: Produce a concise, read-only context handoff for Job Radar that another agent (Claude Code, ChatGPT or similar) can act on without this conversation - repository and HEAD, working tree, current milestone, recent work, decisions, latest verification evidence with its provenance, known defects, next action, and what is authorized versus what needs approval. Use when asked for a handoff, a status summary for another agent or session, or to recover and restate where the project stands. Never edits files or runs checks.
---

# project-handoff

Write a handoff another agent can trust. It reports; it does not verify, fix
or plan new work.

## Hard rules

1. **Read only.** Do not edit, create or delete files, in the repository or
   outside it. No `git add`, `commit`, `stash`, `checkout`, `reset`, `pull`,
   `fetch` or `push`.
2. **Run no gates.** Do not run Ruff, mypy, pytest, `make` targets,
   migrations, Docker or anything that touches PostgreSQL. Verification
   belongs to `verify-change`.
3. **No claimed results without evidence.** Report a check as passing in
   this session only if it ran in this session with a captured exit code
   and completion output (verify-change, H-004). Everything else is
   historical evidence: give its source and the commit it applies to.
4. **Label every claim.** Each item is **Verified** (checked now, against
   Git or a file read now), **Reported** (from a document, a commit message
   or the conversation, not checked now) or **Assumed** (inference; give the
   reasoning). If unsure, use the weaker label.
5. **Do not invent.** If something is not recorded, say "not recorded".
6. **Fresh reads only.** Every Verified claim comes from a command run or a
   file read during this handoff. The Git status and file contents shown at
   session start, and anything remembered from earlier in the conversation,
   are snapshots: never report them as current.

## 1. Collect

Read-only commands:

```
git remote get-url origin
git rev-parse HEAD
git branch --show-current
git status --porcelain=v1 --untracked-files=all
git log --format='%h %ad %s' --date=short -15
git rev-parse --short origin/master
git log --oneline origin/master..HEAD
git log -1 --format='%h %ad' --date=short -- docs/PROJECT-STATE.md
```

Then read:

- `docs/PROJECT-STATE.md` in full.
- In `docs/DEVELOPMENT-LOG.md`, only the entries for the current and the
  last two completed milestones.
- `CLAUDE.md`: Verification, Important invariants, Current scope.
- `.claude/skills/verify-change/SKILL.md`: hard rules only.
- Any SPEC section or ADR that the next planned step names, only to cite it.

The `origin/master` comparison uses the local tracking ref. Without a fetch
it may be stale: say so rather than claiming what is or is not pushed.

## 2. Check the snapshot

- List the commits after the snapshot with
  `git log --oneline <sha>..HEAD`, where `<sha>` is the last command's
  output. If any are listed, the snapshot is stale: say so, list them, and
  compare them with the snapshot's milestone status. Do not update the file.
- If the last `git log` command prints nothing, or `git status` lists
  `PROJECT-STATE.md`, its content is uncommitted. Say so. It describes the
  working tree, which may be ahead of HEAD; for example, a milestone that is
  marked complete but whose commit does not exist yet. Report that milestone
  as uncommitted, not complete.
- A milestone's commit is found the way its DEVELOPMENT-LOG entry says,
  usually a `git log` command. Run it, read-only, and report the SHA.
- If the working tree is dirty, list the paths and say which milestone they
  appear to belong to, labelled Assumed.
- If PROJECT-STATE, DEVELOPMENT-LOG and Git disagree, report the conflict.
  Git wins for what was committed and when. A disagreement Git cannot
  settle, such as a milestone's status, goes under section 5 as unresolved;
  do not pick an answer.

## 3. Authorizations

- **Standing rules** come from `CLAUDE.md` and the verify-change hard rules,
  for example: approval before every integration run, no Docker start or
  stop, no PASS without captured evidence.
- **Granted in this conversation**: quote the grant and its scope (for
  example, "one commit, these four files"). A grant that was used, such as a
  commit already made or an integration run already executed, is consumed:
  say so.
- **Everything else requires approval**, including starting the next
  milestone, committing, pushing, and any integration run.

## 4. Write the handoff

Use this template. Keep it under about 80 lines; link, do not paste.

```
## Job Radar handoff: <date>

Repository: <origin URL> · branch <name> · HEAD <full sha> (<subject>)
Working tree: <clean | paths> · origin/master (local ref, may be stale): <sha>, <N> commits ahead
Snapshot: PROJECT-STATE.md last changed at <sha> (<date>) · <current | stale: commits>

### 1. Current milestone
### 2. Recently completed work            (commit SHAs)
### 3. Relevant decisions                  (ADR numbers, SPEC sections; one line each)
### 4. Latest verification evidence
    | Check | Result | Tree/commit | Source | Label |
    Limits: what was not run, skipped or unverified, and why.
### 5. Known defects and open questions
### 6. Next planned action                 (and that it needs approval)
### 7. Authorizations
    Standing rules · granted in this conversation (consumed or not) · requires approval
### 8. Where to look                       (files and commits for deeper investigation)

Labels: Verified = checked now · Reported = from a document, commit or the conversation · Assumed = inferred
```

Each item in sections 1–7 carries one label.
