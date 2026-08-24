# Slumdog Living Handoff

**Last updated:** 2026-08-24 (UTC)
**Branch:** `arena/01a033af-slumdog`
**HEAD SHA:** `2e3daa40b60ed520a0bcb2f178ef4219fad4d026` (main merge of PR #5, before Milestone 0 commits)
**Phase:** Milestone 0 — repository truth and documentation governance; training frozen
**Mission:** Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.

This file is a living continuation record. Update it as evidence is gathered and again immediately before final merge.

## Product Invariants (from AGENTS.md)

- Target is UNDERDOG_WIN outright win only; never selects draws; draw = failed UNDERDOG_WIN in draw-capable sports.
- Odds optional metadata only — not required, not model feature, not eligibility gate, missing odds must not lower confidence.
- Never force daily pick; NO_STRONG_UNDERDOG valid; never promise profit/guaranteed wins.
- Training frozen until user approves dataset/target/timing/validation contract.
- Main is only permanent branch; Arena branch is delivery only; never force-push main.

## Work Completed in This Session (Milestone 0)

### A. Move STATE.md into docs

- Executed `git mv STATE.md docs/STATE.md` — verified via `git status --short` shows `R  STATE.md -> docs/STATE.md`
- Updated references: `README.md` now references `docs/STATE.md` (not root); `AGENTS.md` read order points to `docs/STATE.md`; `docs/README.md` and `docs/STATE.md` self-document canonical path
- Proof: `grep -Rni --exclude-dir=.git 'STATE\.md' .` shows only `docs/STATE.md` references, no stale root references

### B. Agent Entrypoint AGENTS.md

- Created concise root `AGENTS.md` with:
  - Read order: AGENTS.md → README.md → docs/STATE.md → HANDOFF.md → docs/FOREBET_DEPTH_AUDIT.md → source/tests
  - Permanent product mission
  - 15 invariants (underdog win only, no draws, odds optional, no forced picks, no profit claims, training frozen, main only permanent branch, filesystem separation, change control, doc governance)
  - Verification gates

### C. docs/STATE.md Current Truth Rewrite

- Rewrote from append-only diary to structured current-truth contract containing:
  - current phase, merged work, active blockers, training status, data limitations, next milestone, parked work, unresolved evidence, last verified date (2026-08-24 UTC), links to deeper docs
  - Preserved critical evidence: football 963-date gap, relay Cloudflare 403, Jina Markdown mode works, cricket 0% price verified, American football 0% pending 2026-09-10, handball 2-price fix, hockey 99 dashes sample, MMA 11 void+priced, cross-date identical pairs (basketball:198045/198046, football:2041406, volleyball:96303), hockey 278977 conflict, missing raw bytes for 7 sampled dates, DC token 21, scorer market uncertainty
  - Canonical path documented

### D. Documentation Audit

Inventory under `docs/` (7 files):

| File | Status | Purpose |
|------|--------|---------|
| STATE.md | CURRENT | Canonical current truth |
| FOREBET_DEPTH_AUDIT.md | CURRENT | Training freeze receipt + facet inventory + depth contract + duplicate audits |
| FOREBET_ARCHIVE_DEPTH.json | REFERENCE | Annual archive probe matrix, conservative backfill starts |
| FOREBET_DETAIL_COVERAGE.json | REFERENCE | 3-page-per-sport detail factor sample, justified parser families |
| FOREBET_PRICE_COVERAGE.json | REFERENCE | Representative price snapshot per sport |
| FOREBET_FACET_ANALYSIS_PLAN.md | REFERENCE | Timing classes + 10-step analysis order |
| MA_GOLIDE_ROBBER_FORENSIC.md | HISTORICAL | Legacy Robber forensic spec, useful but not current operating truth |

- **Stale:** None proven obsolete yet
- **Duplicate:** None
- **UNKNOWN:** None requiring user review
- **Proposed removals:** None — do not delete evidence to make tree look clean; ask before deleting UNKNOWN
- **Final canonical read order:** AGENTS.md → README.md → docs/STATE.md → HANDOFF.md → FOREBET_DEPTH_AUDIT.md → other docs → source/tests

### E. docs/README.md Index

- Created `docs/README.md` with purpose/status/last-verified/canonical relationship for each doc, classification report, files moved, links updated, stale/duplicate findings, proposed removals, final read order, checks run, freshness lock rule

### F. README.md Update

- Rewrote to new price-free mission: small daily shortlist, outright underdog wins only, draws fail, odds optional context, no forced picks, no profit claims
- Updated architecture diagram to price-free candidate contract → transparent baselines → daily shortlist → immutable shadow receipts
- Updated candidate contract list (event identity, Forebet fav/underdog, probs, draw prob, model prob, lift, strength, evidence, missingness, status, version, provenance, optional price)
- Updated settlement rules (two-way vs draw-capable, equal-prob handling, voids excluded)
- Updated doc policy: AGENTS.md first, docs/STATE.md canonical, freshness lock

### G. Freshness Lock

- Documented in AGENTS.md, README.md, docs/README.md, docs/STATE.md: every substantive PR must update when applicable docs/STATE.md, HANDOFF.md, docs/README.md, relevant audit doc

## Verification Completed (This Session)

- **Full pytest:** 192 passed (measured via `python3 -m pytest -v`, collection 192 tests, 54.73s) — same count as prior Codespace measurement via hook `EXACT_COLLECTED_TESTS=192` at commit `8e292046340886bac087a9b3bb71372ebe8e2058`
- **Focused tests:** all 192 passed; no separate focused subset needed for doc-only changes but compile/lint gates run
- **Compile:** `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py` — passed
- **Lint:** `python3 -m pyflakes scripts src/slumdog tests` — passed, no findings
- **Diff-check:** `git diff --check` — passed (fixed trailing whitespace)
- **Status:** `git status --short` shows `M README.md`, `R STATE.md -> docs/STATE.md`, `?? AGENTS.md`, `?? docs/README.md`
- **Data-bearing Codespace checks:** Not run in Arena (Arena lacks retained `data/raw` and `data/reports` ledgers). Prior Codespace verification at `e1a7716a99ae4e545b57c04df7d26cb57c1269fb` recorded 192 tests passed. For this doc-only PR, Codespace re-run recommended: `python -m pip install -e '.[dev]' && python -m pytest -q`

## Changed Files (This Milestone 0)

- `STATE.md` → `docs/STATE.md` (git mv)
- `AGENTS.md` (new)
- `docs/STATE.md` (rewritten from diary to current-truth contract)
- `README.md` (rewritten to price-free mission)
- `docs/README.md` (new index)
- `HANDOFF.md` (this file, updated for new mission and verification)

## Open / Parked / Unresolved

**Open (Milestone 0 closure):**
- User review of doc classification before any deletions (required by prompt)
- Commit + push this branch, open PR from `arena/01a033af-slumdog`

**Parked:**
- American football odds probe `scripts/probe_american_football_odds.py` — do not run before ~2026-09-10
- Complex ensembles — baselines first after unlock
- Esoccer separate audit (player-handle identity, no dated archive)
- Dropped football getrs.php keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization

**Unresolved Evidence (preserved from prior audit):**
- 4 cross-date normalized-identical pairs: `basketball:198045`, `basketball:198046`, `football:2041406`, `volleyball:96303` — rescheduled vs date-boundary? Unresolved, not auto-deleted
- Hockey `278977` (2023-08-20) same-key conflicting results 1-6 vs 0-4 — needs source bytes
- MMA 11 rows both void+priced — plausible pre-scratch but unverified (7 raw captures absent)
- Absent sampled raw bytes for 7 suspicious dates — manifest retains URL/byte-count/SHA256 only
- Football DC token `21` raw, unnormalized, preserved
- Scorer market subtype unknown; display order preserved but not ranking; empty rows emit nothing; fill-rate unknown
- Football 963-date backfill gap quantification + replay feasibility

## PR State

- **Current branch:** `arena/01a033af-slumdog` (delivery)
- **Base:** `main` @ `2e3daa40b60ed520a0bcb2f178ef4219fad4d026` (merge of PR #5)
- **PR not yet opened** — will be opened from this branch after this handoff commit
- **Mergeability:** No conflicts expected (doc-only changes + move)
- **User authorization:** Not yet given — do not merge until user explicitly authorizes after reviewing doc audit

## Evidence Language Compliance

- Verified from code: `git mv` operation, `grep` results, file lists
- Verified from executed probe: pytest 192 passed, py_compile passed, pyflakes passed, diff-check passed
- Plausible but unverified: none claimed as verified in this doc-only change
- Unresolved conflict: retained competing facts (duplicate audits) without silently choosing one

## After Merge: Next Session Starts Here

Read `AGENTS.md` first, then `README.md`, then `docs/STATE.md`, then `HANDOFF.md`, then `docs/FOREBET_DEPTH_AUDIT.md`, then relevant source/tests.

**Exact next task (Milestone 1 — audit existing underdog machinery):**

DISCUSS BEFORE CODING. Inspect existing implementation and report what already exists for:
- candidate generation
- favorite/underdog assignment
- feature construction
- historical labels
- training
- calibration
- ranking
- reporting
- daily scheduling
- settlement
- model cards
- shadow/paper outputs

Key files likely include:
- `src/slumdog/contracts.py`
- `src/slumdog/magolide.py`
- `src/slumdog/training.py`
- `src/slumdog/research.py`
- `src/slumdog/pipeline.py`
- `src/slumdog/history.py`
- `src/slumdog/feature_contracts.py`
- sport-specific modules
- tests covering candidates/training/reports
- `docs/STATE.md`
- `HANDOFF.md`

Produce gap analysis against price-free candidate contract:

```
event identity
sport
event date/time
Forebet favorite
Forebet underdog
Forebet favorite probability
Forebet underdog probability
draw probability where applicable
model underdog-win probability
lift over Forebet underdog probability
strength score
supporting evidence
contradicting evidence
missing evidence
candidate status
model/version identifier
source/provenance identifiers
optional price context
```

Do not implement contract until user approves audit.

**Required evidence for next session:**
- Current `magolide.py` underdog cascade still odds-first (higher odds = underdog) vs new mission (lower Forebet prob = underdog)
- `contracts.py` `PriceState` and `CandidateState` (SHADOW_UNPRICED/PRICED/CERTIFIED) vs new statuses (STRONG_UNDERDOG/WATCHLIST/INSUFFICIENT_EVIDENCE/REJECTED_SOURCE_CONFLICT/NO_STRONG_UNDERDOG)
- `feature_contracts.py` `MODEL_TRAINING_ALLOWED = False` — remains frozen
- `training.py`/`research.py` walk-forward vs random split, Brier, calibration
- Settlement handling for draws (currently `winner_index 0` draw) — must count as failed UNDERDOG_WIN

**Safe commands:**
- `git status --short`
- `git diff --check`
- `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`
- `python3 -m pyflakes scripts src/slumdog tests`
- `python3 -m pytest -q` (or `python -m pytest -q` in Codespace after install)
- `grep -Rni --exclude-dir=.git 'STATE\.md' .`
- Read-only local audits of `src/slumdog/*.py`

**Prohibited probes:**
- Do not run `scripts/probe_american_football_odds.py` before ~2026-09-10
- Do not fetch Forebet aggressively; use retained bytes where possible; at most 6 workers, 62s pauses
- Do not train models (training frozen)
- Do not auto-rewrite legacy ledgers
- Do not infer undocumented market semantics

**Unresolved facts to preserve:**
- Four cross-date identical pairs, hockey 278977 conflict, MMA 11 void+priced, absent raw bytes, DC token 21, scorer semantic uncertainty
