# ADR-0012: File-based board configuration

- Status: Accepted
- Date: 2026-09-15

## Context

The set of crawled boards has to live somewhere. The options were a
version-controlled file, a database table managed at runtime, or an admin UI.

## Decision

Configured boards are declared in `config/boards.yml` under version control.
Each entry carries:

- `source`
- `board_token`
- `display_name`
- `enabled`

The file is parsed and validated with Pydantic at startup, then reconciled into
the `companies` table at the start of each run. It is the sole source of
configured boards for the MVP.

Database-managed configuration and an admin UI are out of scope.

## Consequences

- Adding or disabling a board is a reviewable commit with history, not an
  undocumented production mutation.
- The `companies` table is derived state, not an input. It exists to give jobs
  and crawl outcomes a stable foreign key (ADR-0007), not to be edited.
- Reconciliation must be decided explicitly: a board removed from the file is
  marked disabled rather than deleted, so its historical jobs and crawl
  outcomes keep their foreign keys.
- An operator cannot change the board list without a deploy. Acceptable at MVP
  scale; revisit if board churn becomes frequent.
- `display_name` is mutable metadata; editing it must not create a company or
  affect job identity.
