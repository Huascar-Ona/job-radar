# ADR-0017: Deactivation on verified absence

- Status: Accepted
- Date: 2026-11-12

## Context

ADR-0004 deferred job deactivation and constrained whoever introduced it: it
may only reason about a job's absence from a crawl of that company that
**succeeded**, a failed crawl must never touch the company's jobs, and its
2026-11-10 addendum adds that success is not proof of absence (an all-invalid
crawl, or one whose completeness was not verified, proves nothing). SPEC
recorded the blocker: whether a fetch's completeness was verified was not
recorded anywhere.

Until now nothing set `is_active = false`, so a job closed on its board
stayed active forever and `job-radar jobs` kept listing it.

## Decision

A company's active jobs that are absent from a crawl are deactivated, inside
that company's success transaction, only when every guard holds.

**Absence** is set membership, not time: a stored job of company C is absent
when its `source_job_id` is not among the ids of this fetch, which are the
ids of its valid postings, of postings that conflicted with another company,
and of invalid postings whose id is known and canonical. Comparing
`last_seen_at` with the run's instant would depend on the clock.

**Order, in C's single success transaction:**

1. Count **P**, C's active jobs before this crawl, before any upsert.
2. Upsert every posting, as before.
3. Evaluate the guards. The first that fails is the skip reason:

   | Guard | Skip reason |
   |---|---|
   | Completeness verified (the collector reports it; for Greenhouse, `meta.total` present and equal to the number of jobs) | `completeness_unverified` |
   | At least one posting fetched | `empty_board` |
   | Every invalid posting has a canonical id | `invalid_posting_without_id` |
   | At least one valid observation | `no_valid_observations` |
   | Not a large disappearance (below) | `large_disappearance` |

4. Count **K**, C's active jobs absent from this fetch.
5. If no guard failed, set those K jobs `is_active = false`,
   `deactivated_at = observed_at`.
6. Write C's success record with the deactivated count and whether
   completeness was verified.

With P = 0 there is nothing to deactivate: no guard is evaluated and nothing
is logged.

**Large disappearance.** Skip when `2·K > P` and `K ≥ 5`, in integer
arithmetic. Exactly half is not more than half, so P = 10, K = 5 deactivates;
P = 10, K = 6 skips; P = 4, K = 4 deactivates. Both counts are stored jobs,
never fetched postings. Nothing is remembered between runs: the next crawl
recomputes P and K, and there is no confirmation or override mechanism.

**Canonical ids from invalid postings.** A normalizer's
`NormalizationError.source_job_id`, when set, is exactly the id a valid
posting would store. Greenhouse reports it only for an integer `id`, as its
decimal text, which is how a valid posting stores it; anything else (a
string, a boolean, a missing id) is reported as unknown. The service also
rejects any reported id that is not a non-blank string without surrounding
whitespace. An unknown id means the job it belongs to cannot be protected, so
the company is not deactivated in that crawl.

**A skip is not a failure.** The upserts and their counters stand, the
company is recorded `success`, the deactivated count is 0, and one structured
log record names the reason (WARNING for `large_disappearance`, INFO
otherwise) with P and, once known, K. Skip reasons are logged only, not
persisted.

**Reactivation.** A deactivated job that is observed again for the same
company is active again and its `deactivated_at` is cleared, by the existing
upsert. It counts as `existing`.

**Recorded state.** `jobs.deactivated_at` (nullable); `jobs_deactivated` on
`crawl_runs` and `crawl_company_results`; and
`crawl_company_results.completeness_verified` (nullable: null for a failed
company). The `Collector` protocol returns the postings with the
completeness flag (`FetchedBoard`). Rows are never deleted.

## Consequences

- A job closed on a board whose crawl is verified complete stops being listed
  by `job-radar jobs`; its row, `first_seen_at`, `last_seen_at` and
  `raw_data` remain.
- A failed, truncated, unverified or suspicious crawl changes no `is_active`.
  A board without `meta.total` is never deactivated; that is accepted.
- Any database failure while counting or deactivating rolls back the whole
  company, its upserts included, and records it `failed`, as for any other
  persistence failure.
- `deactivated` counts stored jobs, so it stays outside
  `fetched = new + existing + invalid`.
- A board whose every posting genuinely closes keeps its jobs active, because
  an empty board is never taken as proof. A company that loses most of its
  postings at once also keeps them until a later crawl falls under the
  threshold.

This decision satisfies ADR-0004's constraints rather than replacing them;
ADR-0004 stays Accepted.
