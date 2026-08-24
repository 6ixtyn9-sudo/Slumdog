# Slumdog living handoff

Last updated: 2026-08-24
Branch: `arena/01a03377-slumdog`
Pull request: none opened for this follow-up
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

## After merge: next session starts here

PR #4 is the completed integrity checkpoint and was explicitly authorized for
merge on 2026-08-24. The next session should start from updated `main`, read
this file and `docs/FOREBET_DEPTH_AUDIT.md`, and keep model training frozen.

Next priorities, discussion before coding:

1. Finish the retained DOM field map for `#dbc_table .rcnt` and
   `#gscr_table .rcnt`. Verified so far: double-chance probability/pick are
   captured, its single selected-pick American coefficient is dropped, and
   goalscorer containers expose three player predictions that are currently
   dropped. Do not infer first/anytime semantics. Preserve the observed raw
   DC token `21` until its meaning is verified.
2. Quantify the 963-date football backfill gap and retained-capture replay
   feasibility before fetching anything.
3. Audit sparse pricing markup for hockey, rugby, volleyball, handball, and
   baseball using retained bytes first.
4. Audit dropped football getrs.php keys from retained captures where possible.
5. Perform a separate read-only esoccer depth assessment.
6. Run the American-football odds probe only on or after approximately
   2026-09-10.

Unresolved legacy evidence must stay unresolved: four cross-date normalized-
identical pairs, `hockey:278977`'s conflicting results, absent raw bytes for the
sampled suspicious dates, and the 11 MMA void/priced rows whose pre-scratch
explanation is plausible but unverified. Never rewrite those ledgers or invent
provenance without explicit authorization and source bytes.

## Durable operating guide and evidence standard

This section is a mandatory continuation contract. Treat it as an engineering
deliverable, not an informal summary. It must be updated before a merge and
must retain evidence needed to continue without reconstructing it from chat.

### Repository and workspace workflow

- `main` is the only permanent branch. Arena work is delivered from the
  session-assigned `arena/...` branch; do not create another permanent branch,
  rewrite `main`, force-push, or delete unmerged work.
- Keep one coherent PR open for active-session work. Do not merge it without
  explicit user authorization. Immediately before a user-authorized merge,
  update this file, run/record verification, commit and push the final handoff,
  and confirm PR mergeability.
- Arena checkout: `/home/user/Slumdog`. User Codespace checkout:
  `/workspaces/Slumdog`. They are separate filesystems. Uncommitted files,
  ignored files, captures, and ledgers do not cross this boundary.
- Tracked Git changes transfer only via commit/push/pull. Never assume a file
  visible in Codespace is visible in Arena. Inspect retained Codespace data
  before proposing a network refetch.
- For small evidence, produce a compact structured report under `/tmp` and
  paste/attach it. When exact bytes matter, transfer only relevant retained
  files and record their SHA-256; a compact report is not equivalent to source
  bytes. Never commit raw captures, ledgers, temporary archives, or secrets.
- After an authorized merge, the user may safely run in Codespace:
  ```bash
  cd /workspaces/Slumdog
  git status --short
  git checkout main
  git pull --ff-only origin main
  git branch -d <merged-arena-branch>
  git push origin --delete <merged-arena-branch>
  git fetch --prune origin
  git status
  git branch -a
  ```
  Lowercase `git branch -d` is intentional: it refuses to delete unmerged work.

### Evidence language

Classify claims precisely:

- **Verified from code:** name exact file/function/selector (and line context
  when useful).
- **Verified from retained bytes:** record source paths, available SHA-256,
  selectors, and representative observed values.
- **Verified from an executed live probe:** record date, URL, route, request
  count, outcome, and rate-limit behavior.
- **Derived from a ledger:** name ledger path, row counts, dedupe key, and
  provenance limitations.
- **Plausible but unverified:** explain why no source can prove it.
- **Unresolved conflict:** retain competing facts; never silently choose one.

Do not call chat memory, navigation labels, assumptions, or missing raw bytes
"confirmed." Newly fetched bytes are never a replacement for a missing
historical capture.

### Change control, parser, and network rules

- Discuss findings before coding unless the user explicitly authorizes a change.
  No drive-by refactors or speculative abstractions.
- Model training remains frozen until the user explicitly unlocks it. Forebet is
  the sole external source. Preserve immutable captures, never fabricate
  provenance, never automatically rewrite/compact legacy ledgers, and fail
  loudly on conflicting source facts.
- Missing stays missing; never zero-fill. Do not infer market semantics absent
  from source labels. Prefer selector-scoped parsing where stable DOM exists.
  Every parser change needs a minimal fixture-based regression test.
- Prefer retained bytes before network access. Use existing collector/relay code,
  never ad-hoc scraping; at most six workers, small batches, and 62-second
  inter-batch pauses. One-off probes are sequential/minimal and must record URL,
  date, route, and result. Do not run the NFL odds probe before approximately
  2026-09-10.

### Required handoff content and verification gates

For every complete/open/parked/unresolved item, record: status; problem/root
cause; exact files/functions; implemented behavior; test names; commands and
results; evidence paths/values; unresolved edge cases; prohibited assumptions;
and precise next action. DOM work additionally records root/row selectors,
field selectors, observed values, dash/missing behavior, ambiguous tokens,
count/order assumptions, and test fixture shapes.

Before push, run and record:
```bash
python -m pytest -q
python3 -m py_compile scripts/*.py src/slumdog/*.py
pyflakes scripts/*.py src/slumdog/*.py tests
git diff --check
git status --short
```
If unavailable, record the exact failure and give the user the precise
Codespace command. Before merge also record branch/HEAD, diffstat/files, test
count, compile/lint/diff-check results, data-bearing Codespace audits, parked
items, PR mergeability, and explicit user authorization.

## DOM market field-map checkpoint (2026-08-24)

Status: implemented on the active Arena branch; unmerged at this writing.
Evidence: compact DOM report generated in the user Codespace from five retained
`data/raw/details/football/*.html` captures and pasted into the session. It is
verified retained-byte evidence, but those source bytes are not present in the
Arena filesystem.

### Verified selector map

```
#dbc_table .rcnt
  probability: .fprc .fpr
  raw pick: .predict .forepr
  selected-pick coefficient: .prmod .lscrsp

#gscr_table .rcnt
  probabilities: .fprc > .playerPred
  names: .predict .forepr .playerPred
  coefficients: .prmod .lscrsp
```

A `.rcnt` is one fixture row in both tables. In the scorer table, one fixture
row contains up to three probability/player descendants; it is not three scorer
rows. Observed retained examples:

```
DC: 85% / 12 / -1111; 76% / 12 / -435; 77% / 12 / -;
    70% / 21 / -556; 73% / 12 / -345
Scorers: 19%,18%,18% <-> Tulio,Dulay,Tesar <-> -,-,-
         46%,24%,24% <-> Ouattara,Labeau,Stewart <-> -,-,-
         18%,12%,12% <-> Bačić,Krilanovich,Pudić <-> -,-,-
```

`21` remains raw and unnormalized. The DC coefficient prices one selected pick,
not a matrix. Scorer market subtype is unknown; display order is preserved but
not asserted to be a ranking. Empty scorer rows emit nothing. Sample population
fill-rate remains unknown.

Implementation: `src/slumdog/detail_facets.py` adds a small soup-based helper
called from the football branch of `parse_detail()`. It scopes DC/scorer parsing
to the roots above; `_football_detail_stats(text)` retains text-only
corners/cards extraction and no longer uses a global double-chance regex.
Typed DC fields are `doublechance_prob`, `doublechance_pick_raw`,
`doublechance_pick` (only `1X`, `12`, `X2`), and
`doublechance_pick_price_am` (signed token only). Typed scorer fields pair
probabilities/names only when nonempty, equal count, valid percentages, and at
most three; prices are optional signed tokens. No model feature or market
semantic is added.

Regression fixtures: `tests/test_football_dom_markets.py` covers standard DC,
raw `21`/dash behavior, aligned scorers, empty rows, mismatch suppression, and
out-of-root lookalikes. `tests/test_detail_facets.py` now proves the established
text extractor still coexists with DOM-scoped DC extraction.

## Current-session verification (unmerged DOM market implementation)

- `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`: passed.
- `git diff --check`: passed.
- `python -m pytest -q`: not run; Arena Python lacks `pytest` (`No module named pytest`).
- `pyflakes scripts/*.py src/slumdog/*.py tests`: not run; `pyflakes` executable is absent.
- A direct parser smoke test also could not import `slumdog.detail_facets` because Arena Python lacks `bs4` (`No module named 'bs4'`). This is an environment dependency failure, not a parser result.

Run in the dependency-equipped Codespace before merge:
```bash
cd /workspaces/Slumdog
python -m pytest -q
python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py
pyflakes scripts/*.py src/slumdog/*.py tests
git diff --check
```


## After merge: next session starts here

Read `HANDOFF.md` first, then `docs/FOREBET_DEPTH_AUDIT.md`, then the DOM helper
in `src/slumdog/detail_facets.py` and `tests/test_football_dom_markets.py`.
Model training remains frozen.

1. If the DOM-market implementation is still unmerged, run the mandatory gates
   in an environment with project/dev dependencies, review the resulting diff,
   update this handoff with exact results, and await explicit merge approval.
2. Quantify the 963-date football backfill gap and retained-capture replay
   feasibility before fetching anything.
3. Audit sparse hockey/rugby/volleyball/handball/baseball pricing from retained
   bytes; then audit dropped football getrs.php keys and esoccer separately.

Safe read-only commands include `git status --short`, `git diff --check`, the
compile/lint/test gates above, and local retained-data inventory. Do not run the
American-football odds probe before approximately 2026-09-10. Do not fetch to
replace missing historical bytes. Arena currently lacks the five retained
football detail captures; the compact Codespace DOM report is recorded above,
but exact-byte review requires the user to transfer relevant bytes.

Do not "fix" unresolved legacy evidence without new source bytes: four
cross-date normalized-identical pairs, hockey `278977` conflicting results,
absent sampled raw bytes, and 11 MMA void/priced rows with only a plausible
pre-scratch explanation. Preserve raw DC token `21`, scorer-market semantic
uncertainty, and unknown scorer sample fill rate.
