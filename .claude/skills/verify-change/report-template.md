## verify-change report

**Overall: <PASS | FAIL | BLOCKED | PARTIAL>**
HEAD `<sha>` · working tree: <clean | N changed, M untracked>

### Scope
- Code-affecting paths: <list, or "none">
- Docs/harness paths: <list, or "none">
- Integration required: <yes | no> (<reason>)
- Higher-risk scope: <schema | dependencies | tests changed with src | none>

### Source tree
- Toplevel: <path> · `job_radar` imported from: <path> · <matches | MISMATCH>

### Checks
Exit is the invoked command's code; for `make` targets add the tool's, e.g.
`2 (pytest 1)`.

| Check | Required | Status | Command | Exit | Evidence |
|---|---|---|---|---|---|
| LINT | yes | | `.venv/bin/ruff check .` | | |
| FORMAT | yes | | `.venv/bin/ruff format --check .` | | |
| TYPECHECK | yes | | `make typecheck` | | |
| UNIT | yes | | `make test-unit` | | |
| INTEGRATION | <yes/no> | | `make test-integration` | | |
| DOC-IMPACT | <yes/no> | | read-only review (section 5a) | n/a | <N documents reviewed> |

### Documentation impact (agent judgment, not a deterministic check)
| Document | Sections reviewed | Verdict | Reason |
|---|---|---|---|
| <path> | <sections> | <updated / unchanged-accurate / contradiction-introduced / debt / cannot-evaluate> | <why; for a contradiction, quote the document and name the code it disagrees with> |

- Search terms: <terms searched in docs, README.md, CLAUDE.md, .claude>
- Pre-existing documentation debt (does not fail the gate): <list, or "none found">
- <"Not required: no working-tree change" for a clean tree>

### Failures
<For each FAIL: failing node ids or rule codes and the first relevant output
lines. "None" otherwise.>

### Blocked / skipped
<For each: the reason, probe output with credentials removed, and the
remediation the user can run.>

### Database target (only if integration was considered)
- URL source: <environment | .env or default>
- Driver / host / port / database: <...>
- Test databases (from tests/conftest.py): <...>
- Destructive operations confirmed in tests/support/database.py: <...>
- Safety gate: <passed | failed: reason>
- Approval: <requested and given | requested and declined | not requested: reason>

### Not verified
- Acceptance tests (ADR-0010 layer 4): none exist in the repository.
- <skipped/xfailed tests, stale-venv risk, non-required checks skipped, ...>

### Side effects
- `git status` before vs after: <identical | differences>
- Git-ignored caches written: <.ruff_cache, .mypy_cache, .pytest_cache, __pycache__>
- Databases touched: <none | jobradar_schema_test, jobradar_migration_test on host:port>
