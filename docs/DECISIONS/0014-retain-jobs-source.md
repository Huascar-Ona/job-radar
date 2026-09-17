# ADR-0014: Retain jobs.source despite being derivable from company

- Status: Accepted
- Date: 2026-09-16

## Context

`jobs` stores `source` even though `jobs.company_id -> companies.source` already
determines it. That is textbook redundancy, and it was challenged directly:
should identity be `(source, source_job_id)` or `(company_id, source_job_id)`?

Answering it requires knowing the scope within which a provider's posting id is
unique. We investigated the three planned sources: official documentation
first, live public responses second (~1,500 postings across 8 boards).

### Documented guarantees

Only one, and it is about structure rather than scope:

- **Greenhouse.** The Job Board API documents that `id` identifies the job
  **post** while `internal_job_id` identifies the **job** itself, and that
  prospect posts carry a null `internal_job_id`.

That is the complete list. Specifically, **no provider documents the scope
within which its posting id is unique**:

- **Greenhouse** does not state whether `id` is unique per board or provider-wide.
- **Lever** documents only `id: Unique job posting ID`, with no scope, format
  or stability guarantee.
- **Ashby** does not document an `id` field at all, though its API returns one.

### Observed behavior (live responses, 2026-09-15)

Reproducible at the time of writing; not promised by anyone.

- No cross-board id collisions in ~1,500 postings.
- **Greenhouse** ids interleave across boards: merging Duolingo's and GitLab's
  305 ids into one sorted sequence yields 91 board transitions, the signature
  of a shared allocation sequence rather than per-board counters. However,
  Robinhood's ids are 7 digits in a disjoint range without the `002` suffix the
  other two share, so the space is sharded or has eras, not one uniform counter.
- **Greenhouse** returns multiple posts per job *within a single board*:
  Duolingo showed 83 posts across 55 distinct `internal_job_id`, and one
  Robinhood job carried three posts.
- **Lever** and **Ashby** ids are UUID-shaped in 100% of samples.
- Board tokens are case-insensitive for Greenhouse and Ashby, case-sensitive
  for Lever.

### Assumptions we cannot safely make

- That any provider guarantees ids unique beyond a single board.
- That ids are immutable or never recycled.
- That a removed-and-reposted job keeps its id. Greenhouse's post-vs-job split
  suggests it does not.
- That id formats stay uniform. Two shapes were already observed on Greenhouse.

## Decision

Keep `jobs.source`, and keep `UNIQUE (source, source_job_id)` as job identity.

Reasoning:

1. **Provider uniqueness scope is undocumented.** Neither the provider-scoped
   nor the board-scoped reading can be proven from documentation, so the choice
   is made on consequences, not on guarantees.
2. **`UNIQUE (source, source_job_id)` gives provider-scoped posting identity.**
   One posting is one row no matter how many configured boards expose it.
3. **Removing `source` would change identity to company-scoped.** Identity
   would become `(company_id, source_job_id)`, and the same posting reachable
   through two configured boards would become two rows.
4. **Duplicate postings harm Job Radar more than controlled redundancy does.**
   The product's output is "here are the jobs that appeared"; duplicates
   corrupt exactly that. The failure modes are not symmetric: provider-scoped
   identity fails by reassigning one row's `company_id`, which is visible and
   self-correcting, while company-scoped identity fails by silently emitting
   the same job twice.
5. **The composite foreign key guarantees the redundancy cannot drift.**
   `(company_id, source) -> companies (id, source)` with RESTRICT on delete and
   update (ADR-0013) makes it impossible for `jobs.source` to disagree with its
   company. Redundancy the database enforces is not drift.

## Consequences

- `jobs.source` stays, as a guaranteed-consistent denormalization rather than a
  free-floating copy.
- Per-source queries need no join; identity lookups are served directly by the
  unique index.
- **A `company_id` change for an existing `(source, source_job_id)` must be
  treated as an ingestion anomaly.** It means either a posting genuinely moved
  between boards, or the same board is configured twice, or the undocumented
  uniqueness assumption has broken. The repository must surface it explicitly
  and must not silently reassign the row. This is the mechanism that converts
  an unprovable premise into an observable event; it is not implemented yet and
  belongs with the upsert.
- Adding Lever or Ashby requires no schema change.

## Follow-ups, deliberately not implemented

- **Board-token normalization.** Greenhouse and Ashby treat tokens
  case-insensitively while Lever does not, so `duolingo` and `Duolingo` in
  configuration would create two companies serving identical postings. The
  rules are source-specific, so normalization belongs with the collectors
  rather than in a blanket lowercase.
- **`source_group_id` / `internal_job_id`.** Greenhouse's `internal_job_id` is
  the only documented handle on "the same job, reposted", and it is currently
  discarded. A nullable, source-agnostic column could carry it later. Recorded
  as a future design consideration only; note it is null for prospect posts,
  and no equivalent has been identified for Lever or Ashby.
