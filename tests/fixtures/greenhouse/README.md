# Greenhouse fixtures

`board_jobs_content.json`
: Response shape captured verbatim from a live public Board API call
  (`/v1/boards/{token}/jobs?content=true`) on 2026-09-15. Field names, types,
  nesting and timestamp formats are real; all identifying values are synthetic.

`board_jobs_degraded.json`
: **Synthetic.** Not observed live. Exercises the "unavailable fields stay
  null" rule, absent vs. explicitly-null optional fields, and an entry with no
  `id` that must be counted as `invalid` without failing the batch.

Observed on 2026-09-15 across three public boards (458 jobs total): no
pagination (`meta.total` equalled `len(jobs)` in every case), `first_published`
present and non-null on 458/458 jobs, `departments`/`offices`/`content`
returned only when `content=true`, and `content` HTML-escaped.
