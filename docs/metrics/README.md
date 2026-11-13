# Harness metrics (H1)

Local, read-only observability for Claude Code development sessions: token
usage, time, activity and verification quality per milestone. The tool lives
in `tools/harness_metrics/`, uses the standard library only, and is not part
of the Job Radar application (neither imports the other; a test enforces it).

Run it from the repository root:

```bash
.venv/bin/python -m tools.harness_metrics sessions             # every transcript, by alias
.venv/bin/python -m tools.harness_metrics session s1           # one session, per turn
.venv/bin/python -m tools.harness_metrics suggest-segments s1  # boundary suggestions
.venv/bin/python -m tools.harness_metrics milestones           # milestone comparison
.venv/bin/python -m tools.harness_metrics validate             # check the config files
```

`--json` gives machine-readable output; `--strict` exits 1 on any parsing
anomaly or format drift. `milestones --only M --write-snapshots docs/metrics/snapshots`
writes the committed snapshots. Nothing ever modifies a transcript.

## What is committed and what stays local

| File | Committed | Holds |
|---|---|---|
| `.harness/metrics.local.toml` | **No** (`.gitignore`) | Real session ids, session aliases, the transcripts directory, milestone segments |
| `docs/metrics/pricing.toml` | Yes | Versioned estimate rates, with source and date |
| `docs/metrics/outcomes.jsonl` | Yes | Verification outcomes and interventions: codes and counts only |
| `docs/metrics/snapshots/<milestone>.json` | Yes | Aggregates per milestone: no session id, alias, path or content |

Real session ids and absolute paths never leave the local file. Output shows
session aliases; `sessions --show-ids` prints real ids, locally, on request.

## Reading the transcripts (privacy)

Claude Code writes `~/.claude/projects/<project>/<session>.jsonl`. Those
files also hold prompts, responses, tool inputs and outputs, file snapshots,
request bodies and account identifiers. The parser reads an allowlist of
metadata only (`tools/harness_metrics/transcript.py`): record type, time,
session id, message id, model, stop reason, numeric usage, service tier and
speed, turn durations and compaction markers, and the *names* of built-in
tools (any other tool name, MCP tools included, counts as `other`). Record
types that carry content are skipped whole. Tests feed synthetic transcripts
whose every content field holds a canary string and assert that no output,
snapshot or error message contains it.

Claude Code prunes old transcripts after its `cleanupPeriodDays` setting, so
write a milestone's snapshot when it closes.

## Counting rules

- One API call per `(session, message.id)`. Claude Code writes a message once
  per content block, each line repeating the same usage. Lines of one message
  whose usage differs are a conflict: that call's usage becomes unknown.
- `usage.iterations` repeats the top-level usage and is never added.
- Thinking tokens are part of output tokens, never added to them.
- Cache writes are kept by lifetime (5 minutes, 1 hour, unspecified).
- `processed input` sums the request input of every call (cumulative
  processing, not context size); `new context` is uncached input plus cache
  writes; `generated output` is output.
- `largest request input` is the largest single request's input as the
  API's usage fields account it (uncached input + cache writes + cache
  reads). It is accounting metadata: it excludes that call's own output and
  says nothing about the size of the model's context window, so it is not a
  measurement of window occupancy.
- Synthetic (`<synthetic>`) and subagent (`isSidechain`) records are
  excluded and counted separately; no subagent records have been observed
  yet, so their format is unverified.
- Missing or inconsistent data is `null` (JSON) or `-`/`unknown` (tables),
  with a reason in `data_gaps`, never 0.

## Milestones

A milestone is one or more *segments*: contiguous ranges of typed-prompt
turns of a session, recorded by hand in the local file. `suggest-segments`
lists the turn in which each commit was made, as a suggestion. A segment is
`inferred` until a person has checked it, then `reviewed`; a snapshot of a
milestone with any inferred segment says so.

Every segment has an explicit last turn. A segment whose last turn lies
beyond the turns recorded so far (for example a placeholder like 99) is
reported with the gap `segment_extends_beyond_recorded_turns`, because it
would otherwise absorb later, unrelated turns. Extend a segment only when
the turns it should cover exist; later turns stay unattributed until mapped.

## Cost

Transcripts record no cost, and token usage is not billing (a subscription
plan does not bill per token at all). `pricing.toml` holds rates per model
and conditions, as decimal strings per million tokens, with the source they
were read from and the date. A rate applies to a call only if the model,
`service_tier`, `speed` and `inference_geo` all match the call's usage
fields and the call's request input lies within the rate's
`min_request_input_tokens`/`max_request_input_tokens` range. A call with no
matching rate, unknown usage, or cache writes of unknown lifetime makes the
milestone's estimate unknown, never zero.

The committed table has no rates, so every estimate is unknown. To enable
estimates:

1. Open the official Anthropic pricing page and find each model you use
   (`.venv/bin/python -m tools.harness_metrics session s1 --json` lists the
   tokens; the models are in `milestones --json`, under `activity.models`).
2. For each model, add a `[[rates]]` table with `model`, `service_tier`,
   `speed` and `inference_geo` exactly as the transcripts report them (in
   the sessions measured so far: `standard`, `standard`, `global`), and the
   per-million-token rates for `input`, `output`, `cache_write_5m`,
   `cache_write_1h` and `cache_read` as decimal strings.
3. State the request sizes each rate covers. If the page prices long
   requests differently, add one table per tier with its
   `min_request_input_tokens` and `max_request_input_tokens`; otherwise set
   `min_request_input_tokens = 0` and `max_request_input_tokens = "none"`.
   Leaving the range out is an error, so a tier cannot be overlooked
   silently.
4. Set `source` to the page's URL, `retrieved` to the date you read it, and
   a new `version`.
5. Run `.venv/bin/python -m tools.harness_metrics validate`, then
   `milestones`. Any unpriced call still shows the estimate as unknown,
   with the reason under `cost.unpriced_reasons` in the JSON.

## Quality

`outcomes.jsonl` records, per milestone, gate runs (`gate`, `attempt`,
`status`), consumed integration authorizations, interventions (by code) and
escaped defects (a count, 0 meaning checked and none found). It admits codes
and counts only. Codes:

- gates: `lint`, `format`, `typecheck`, `unit`, `integration`, `doc_impact`,
  `diff_check`;
- statuses: `pass`, `fail`, `blocked`, `unverified`, `skipped`;
- interventions: `contract_amendment`, `scope_change`, `correction`,
  `rejected_proposal`, `decision_request`;
- sources: `manual`, `backfill`, `verify-change`.

Quality rows report counts, not scores:

- `first runs passed` is `passed/decisive`: first runs of each gate that
  passed, over first runs that passed or failed (blocked and skipped runs
  are not decisive). With so few gates per milestone it is shown as a
  fraction, not a percentage.
- `gate failures` counts failed runs; `failures re-run` counts failures
  whose gate ran again later, each failure once.
- `later runs (any reason)` counts every run of a gate after its first.
  Most are routine re-verification after an unrelated change, not retries:
  the ledger does not record why a run happened.
- `recorded interventions` counts what the ledger records; for backfilled
  milestones that is the contract decisions the maintainer amended, not
  every correction or clarification.
- `quality source` says whether the rows come from `manual`, `backfill` or
  `verify-change` entries.

Token figures are not quality figures: read them side by side, never as a
score.

The entries for 4E, 4F and 4G are backfilled (`source: backfill`) from their
DEVELOPMENT-LOG entries; their interventions count the contract decisions the
maintainer amended. Escaped defects have not been checked for any milestone.
