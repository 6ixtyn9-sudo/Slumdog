# Slumdog State — Canonical Current Truth

**Last verified:** 2026-08-24 (UTC)
**Branch:** `arena/01a033af-slumdog` (delivery), `main` is only permanent branch
**Doc canonical path:** `docs/STATE.md` (moved from root `STATE.md` via `git mv`)

## Permanent Product Mission

> **Slumdog identifies a small daily shortlist of participants that Forebet considers underdogs but whose available pre-event evidence indicates a credible outright-win upset.**

Invariants (from `AGENTS.md`):
- Target is `UNDERDOG_WIN` outright win only; draws never count as success.
- Slumdog never selects draws; in draw-capable sports, draw = failed `UNDERDOG_WIN`.
- Odds are optional metadata only — not required input, not a model feature, not eligibility gate.
- Missing odds must not lower confidence.
- Never force daily pick; `NO_STRONG_UNDERDOG` is valid.
- Never promise profit, guaranteed wins, life-changing results.
- Training frozen until user approves dataset, target, timing, validation contract.

## Current Phase

**Milestone 0 — Repository Truth and Documentation Governance** (pre-authorized).

Completing:
- `STATE.md` → `docs/STATE.md` move + reference cleanup
- `AGENTS.md` entrypoint
- Docs audit/classification + `docs/README.md` index
- Freshness workflow lock

Next: Milestone 1 — audit existing underdog machinery against price-free contract (discuss before coding).

## Merged Work (main @ 2e3daa4)

- PR #4: football truncated relay fix, H2H fabrication guard, MMA duplicate dedup, no-odds documentation (cricket 0% price verified, American football dash handling)
- PR #5: DOM-scoped football double-chance + scorer market facets (`detail_facets.py`), regression tests `test_football_dom_markets.py`
- Core pipeline: immutable captures, per-sport history ledgers (`history_<sport>.jsonl.gz` + manifest), depth-sweep census, `forebet.py` relay handling (Markdown reader mode → html relay → direct), timing classes (`PRE_EVENT`/`LIVE_ONLY`/`RESULT_ONLY`/`UNKNOWN`), sport-specific settlement contracts
- Feature contracts in `feature_contracts.py` (currently aspirational, not training inputs)
- Legacy Ma Golide Robber reproducer in `magolide.py` (odds-first cascade, now superseded by mission)
- 192 tests (collection measured via hook at `8e292046340886bac087a9b3bb71372ebe8e2058` per HANDOFF)

## Active Blockers

1. **Doc governance incomplete** — this file, `AGENTS.md`, `README.md`, `docs/README.md` need to reflect price-free mission before Milestone 1.
2. **Underdog machinery misalignment** — existing `contracts.py`/`magolide.py`/`feature_contracts.py` still encode odds-first identity and legacy score; needs gap analysis vs new candidate contract.
3. **Training frozen** — explicit user unlock required after label/eligibility + feature/timing contracts approved.
4. **Data limitations** (see below) block reliable baselines.

## Current Model-Training Status

**FROZEN.** From `feature_contracts.py`: `MODEL_TRAINING_ALLOWED = False`.

- 14-day preliminary experiment discarded (generic vector, insufficient sport-specific representation).
- No retraining allowed until each sport has: listing/detail facet contract, historical depth receipt, timing classification, settlement coverage, price-availability profile, AND user approves dataset/target/timing/validation contract.
- When unlocked, first implement transparent baselines (Forebet underdog prob, gap, recent-form differential, Ma Golide heuristic, simple interpretable model) with walk-forward validation, never random splits.

## Current Data Limitations

- **Football backfill gap:** 963 dates failed on runner (401 relay) — Markdown reader path added but needs next pipeline probe.
- **Relay egress:** Direct `forebet.com` from Azure (Codespaces + GH runners) behind Cloudflare JS challenge (403). Jina relay Markdown mode works locally (714 rows, 2.4MB) but GH Actions IP 401 status still to be verified.
- **Price coverage (snapshot, not global):** Football 77.3% (778/1006), Basketball 60.6%, Tennis 96%, Baseball 68.4%, Cricket 0% (6643 settled rows, zero priced, verified no `.haodd`), Handball 0% but actually 2-price American format fixed, Hockey 0% on sampled WHL/AHL date (99 dashes) — needs in-season NHL/KHL/SHL re-check, Rugby/Volleyball 0%, American football 0% (7447 archived rows zero priced, pending 2026-09-10 probe), MMA 153 priced / 757 unique, Esoccer rolling board no reliable dated archive.
- **Detail coverage:** Three-page sample per sport in `FOREBET_DETAIL_COVERAGE.json` justified parser families, but full census missingness not yet measured from `depth-sweep`.
- **Legacy ledger integrity:** 279 byte-identical extra rows across 278 repeated same-date keys; 4 cross-date identical pairs after removing `event_date` (`basketball:198045`, `basketball:198046`, `football:2041406`, `volleyball:96303`); hockey `278977` same-key conflicting results (1-6 vs 0-4); MMA 11 rows both void+priced (7 raw captures absent, plausible pre-scratch); all 759 legacy MMA rows lacked `raw_sha256`/`captured_at` (predates provenance retention).
- **Missing raw bytes:** 7 sampled suspicious dates had manifest hashes but no local `data/raw` files; hash cannot reconstruct bytes, refetch ≠ historical capture.
- **Football markets:** 5 distinct JSON endpoints (uo, bts, ht, ah, cards) — one req/date each; htft byte-identical to ht; corners/doublechance/goalscorer echo 1X2 JSON, so price only from detail HTML (with `Coef. -` often).

## Next Approved Milestone

**Milestone 0 completion:**
- Move + update refs (done: `git mv STATE.md docs/STATE.md`, grep proof required)
- `AGENTS.md` with invariants + read order
- `docs/STATE.md` rewritten as current truth (this file)
- Audit docs/ and classify CURRENT/REFERENCE/HISTORICAL/STALE/UNKNOWN
- `docs/README.md` index with purpose/status/last-verified/canonical links
- Update `README.md` to new mission
- Report files moved, links updated, stale/duplicates, proposed removals, canonical read order, checks run
- Stop for user review before deleting docs or changing model logic

**Then Milestone 1:** audit existing underdog machinery (candidate generation, favorite/underdog assignment, features, labels, training, calibration, ranking, reporting, scheduling, settlement, model cards, shadow outputs) vs price-free candidate contract — discuss before coding.

## Parked Work

- American football odds probe (`scripts/probe_american_football_odds.py`) — do not run before ~2026-09-10 (regular season)
- Complex ensembles — baselines first after unlock
- Esoccer separate audit (player-handle identity, no dated archive route)
- Dropped football `getrs.php` keys audit
- Sparse hockey/rugby/volleyball/handball pricing re-check on in-season top-league dates
- Auto-rewrite/compact legacy ledgers — prohibited without explicit authorization

## Unresolved Evidence

- 4 cross-date normalized-identical pairs (basketball:198045/198046, football:2041406, volleyball:96303) — rescheduled vs date-boundary? Unresolved, not auto-deleted.
- Hockey `278977` (2023-08-20) conflicting results — needs source bytes, neither selectable honestly.
- MMA 11 void+priced rows — plausible pre-scratch but cannot verify without raw captures.
- Absent sampled raw bytes for 7 suspicious dates — manifest retains URL/byte-count/SHA256 only.
- Football DC token `21` observed (`85% / 12 / -1111` etc) — raw, unnormalized, preserved.
- Scorer market subtype unknown; display order preserved but not ranking; empty rows emit nothing; sample fill-rate unknown.
- Football 963-date backfill gap quantification + replay feasibility from retained captures.
- HT/FT as single combined price, not 9-cell matrix — verified distinctness but need DOM selector proof.

## Links to Deeper Documents

- `AGENTS.md` — permanent mission + operating constitution
- `README.md` — product overview (to be updated to new mission)
- `HANDOFF.md` — session continuation record
- `docs/FOREBET_DEPTH_AUDIT.md` — training freeze receipt, facet inventory, historical depth contract, price coverage snapshots, verified no-odds gaps, duplicate audits
- `docs/FOREBET_ARCHIVE_DEPTH.json` — annual archive probe matrix (conservative backfill starts)
- `docs/FOREBET_DETAIL_COVERAGE.json` — 3-page-per-sport detail factor sample
- `docs/FOREBET_PRICE_COVERAGE.json` — representative price snapshot per sport
- `docs/FOREBET_FACET_ANALYSIS_PLAN.md` — timing classes + analysis order (10-step)
- `docs/MA_GOLIDE_ROBBER_FORENSIC.md` — legacy Robber forensic spec (authoritative meaning 2, cascade, score, calibration warning, defects)
- `docs/README.md` — (to be created) doc index with status/last-verified

## Verification Receipt (Milestone 0 in-progress)

- `git mv STATE.md docs/STATE.md` executed
- `grep -Rni --exclude-dir=.git 'STATE\.md' .` to be run after README update
- `AGENTS.md` created with invariants + read order
- This file rewritten from diary to current-truth contract
- Tests/checks pending final doc updates: `python -m pytest -q`, `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py`, `pyflakes`, `git diff --check`, `git status --short`

## After Milestone 0

- User reviews doc audit classification before deletions
- Then Milestone 1 gap analysis vs price-free candidate contract (event identity, sport, date/time, Forebet fav/underdog, fav/underdog prob, draw prob, model underdog-win prob, lift, strength score, supporting/contradicting/missing evidence, candidate status, model/version id, provenance, optional price context)
- Do not implement contract until user approves audit
