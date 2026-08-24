# Slumdog living handoff

Last updated: 2026-08-24
Branch: `arena/01a032d8-slumdog`
Pull request: #4
Phase: Forebet depth audit; model training remains frozen

This file is a living continuation record. Update it as evidence is gathered and
again immediately before the final merge.

## Constraints

- Keep PR #4 open until the user explicitly authorizes the single final merge.
- Discuss findings before code changes unless the user already authorized them.
- Forebet is the sole external source. Preserve immutable raw captures and never
  fabricate provenance.
- Be gentle with Forebet: at most 6 workers, small batches, and 62-second pauses
  for collection. Read-only local audits do not use the network.
- Do not modify or unlock model training.
- Avoid drive-by refactors and speculative abstractions.
- Verify factual claims from code, retained bytes, or an executed probe. Mark
  anything else unverified.

## Work currently in PR #4

### Football truncated relay responses

`src/slumdog/forebet.py` now fully parses raw or HTML-wrapped football JSON
before accepting a capture. A truncated HTTP-200 response raises `ValueError`
and the football path retries up to three times. Regression tests are in
`tests/test_forebet.py`.

### H2H fabrication

`src/slumdog/detail_facets.py` restricts score-pair fallback extraction to an
explicit H2H container. Regression tests cover standings-only markup and a real
`.h2h` table. `scripts/audit_detail_h2h.py` was run against the user's retained
captures on 2026-08-24 and reported `suspicious pages: 0`.

### MMA duplicate settlement rows

The user's `data/reports/history_mma.jsonl.gz` contained 759 rows and 757 unique
event IDs. `mma:2638` and `mma:2721` were byte-identical duplicate settled,
priced rows from 2026-06-15. There were no conflicting duplicate dispositions.
Deduplicated figures are 600 settled, 157 void, 153 priced, with 11 rows both
void and priced. The earlier 159/159 equality is not reproduced and was not a
structural equality.

`parse_mma_settled` now deduplicates valid within-page event IDs. `backfill_sport`
also has a run-local `(sport, event_id, event_date)` write guard. Existing
ledgers are not automatically rewritten. `scripts/audit_mma_void_priced.py`
reports stored and unique cross-tabs, duplicate classifications, and provenance
coverage. A regression test is in `tests/test_special_settlement.py`.

### Legacy MMA provenance

All 759 inspected legacy MMA rows lacked `raw_sha256` and `captured_at`. Current
backfill writes `facets.raw_sha256` for new rows. Whether retained MMA raw pages
are sufficient to rebuild the legacy ledger has not yet been established. Do
not invent hashes or rebuild without user approval.

### No-odds documentation and probes

`docs/FOREBET_DEPTH_AUDIT.md` records verified cricket and American-football
coverage findings. `scripts/probe_american_football_odds.py` is parked until on
or after approximately 2026-09-10 and must not be run early.

## Verification completed

- 184 tests passed on both the Arena checkout and the user's data-bearing
  Codespace.
- `python3 -m py_compile scripts/*.py src/slumdog/*.py` passed.
- `git diff --check` passed.
- PR #4 was open and mergeable with no configured GitHub status checks.

## Remaining investigations

These are evidence-gathering tasks. Discuss findings before implementing any
additional fixes.

1. Scan every `data/reports/history_*.jsonl.gz` for duplicate event IDs. Report
   exact versus conflicting duplicates and deduplicated counts. Do not rewrite
   a ledger automatically.
2. Inspect the 11 MMA rows that are both void and priced against retained raw
   captures, where available. Determine whether prices were posted before a
   scratch/no-contest; do not assume.
3. Inventory retained `data/raw/mma/` captures and determine whether they cover
   the dates represented by all 759 legacy rows. Report provenance-rebuild
   feasibility only.
4. Broader adversarial Forebet audit, discussion before coding:
   - detail-only corners, double chance, and top-scorer information;
   - ensure HT/FT is treated as one combined price, not a 9-cell matrix;
   - sparse hockey/rugby/volleyball pricing: coverage truth versus parser miss;
   - the football 963-date backfill gap;
   - separate esoccer audit.
5. Run the NFL odds probe on or after approximately 2026-09-10.

## Final merge procedure

When the user says the work is complete, update this file with final evidence
and a section titled `After merge: next session starts here`. Commit that update
on this branch, then merge PR #4 only with explicit user authorization.

## Cross-sport duplicate findings (2026-08-24)

A local read-only scan of all available `history_*.jsonl.gz` files found 279
byte-identical extra rows across 278 repeated same-date keys. Forebet IDs also
recur across dates for genuinely distinct fixtures, so the valid identity is
`(sport, event_id, event_date)`, never event ID alone.

Four legacy cross-date pairs are identical after removing only `event_date`:
`basketball:198045`, `basketball:198046`, `football:2041406`, and
`volleyball:96303`. Treat these as unresolved; do not auto-delete them. A
same-key conflict also exists for `hockey:278977` on 2023-08-20: the ledger has
incompatible 1-6 and 0-4 results. Neither can be selected without source bytes.

Raw files were absent for seven sampled suspicious dates, although manifest
receipts preserve source URL, byte count, and SHA-256. This is a sampled result,
not a claim that every legacy raw file is absent. Hashes alone cannot restore
provenance and a refetch is not the historical capture.

The backfill write path now validates a complete day before output: exact
same-key payloads collapse, while conflicting payloads raise with identifying
fields before any day rows are appended. `append_settled_from_capture` remains
out of scope because it writes a separate interim artifact.

## MMA void-and-priced raw verification (2026-08-24)

The 11 rows that are both `VOID` and priced span seven dates from 2026-04-19
through 2026-07-25. Each date has a manifest receipt with source URL, byte count,
and source SHA-256, but no corresponding local file under `data/raw/mma/<date>`.
The derived ledger proves the rows contain both fields; it does not prove the
history of when prices were posted relative to a scratch/no-contest. Treat the
pre-scratch explanation as plausible but unverified. Do not refetch and present
new bytes as the historical capture.
