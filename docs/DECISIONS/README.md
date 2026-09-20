# Architecture Decision Records

One file per decision: `NNNN-kebab-case-title.md`.

Status is one of `Proposed`, `Accepted`, `Superseded by ADR-NNNN`.

Records are immutable once accepted. A changed decision is a new ADR that
supersedes the old one; the old file stays and is marked superseded.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-technology-stack.md) | Technology stack | Accepted |
| [0002](0002-job-identity-and-idempotent-upserts.md) | Job identity and idempotent upserts | Accepted |
| [0003](0003-timestamp-semantics.md) | Timestamp semantics | Accepted |
| [0004](0004-defer-job-deactivation.md) | Defer job deactivation | Accepted |
| [0005](0005-no-filtering-during-ingestion.md) | No filtering during ingestion | Accepted |
| [0006](0006-greenhouse-public-board-api.md) | Greenhouse public Board API | Accepted |
| [0007](0007-company-identity-via-board-token.md) | Company identity via board token | Accepted |
| [0008](0008-explicit-crawl-outcome-model.md) | Explicit crawl outcome model | Accepted |
| [0009](0009-sequential-crawling-bounded-timeouts.md) | Sequential crawling with bounded timeouts | Accepted |
| [0010](0010-test-against-real-postgresql.md) | Test against real PostgreSQL | Accepted |
| [0011](0011-raw-payload-retention.md) | Raw payload retention | Accepted |
| [0012](0012-file-based-board-configuration.md) | File-based board configuration | Accepted |
| [0013](0013-source-consistency-composite-key.md) | Source consistency via a composite foreign key | Accepted |
| [0014](0014-retain-jobs-source.md) | Retain jobs.source despite being derivable | Accepted |
| [0015](0015-upsert-outcome-classification.md) | Classify upsert outcomes from the row | Accepted |
| [0016](0016-greenhouse-ingestion-boundary.md) | Greenhouse ingestion boundary, errors and retries | Accepted |
