# ADR-0007: Company identity via board token

- Status: Accepted
- Date: 2026-09-15

## Context

`company` is listed as a normalized job field, but it does not come from the
Greenhouse payload — it comes from our configuration. Company names are also
edited (rebrands, punctuation, legal suffixes), so a name cannot carry identity.

## Decision

A company is identified by `(source, board_token)`.

The display name is mutable metadata. Renaming a company must not affect job
identity, create rows, or orphan history.

Companies live in a `companies` table, reconciled from version-controlled
configuration at the start of each run. Jobs reference companies by foreign
key rather than storing a denormalized name.

## Consequences

- A rename is a single-row update, not a data migration.
- `NormalizedJob.company` carries the display name for the domain model, while
  persistence stores the foreign key. The two are resolved in the repository.
- Job identity stays `(source, source_job_id)`; the company FK is not part of
  the uniqueness constraint.
