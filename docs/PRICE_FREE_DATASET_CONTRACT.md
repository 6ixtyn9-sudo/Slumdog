# Price-Free Dataset Contract — Milestone 4

**Last verified:** 2026-08-24 (UTC)
**Branch:** arena/01a033af-slumdog
**Status:** CURRENT — Milestone 4 price-free historical example builder
**Training:** FROZEN
**Feature contract version:** `price-free-v1-minimal-2026-08-24`
**Label contract version:** `price-free-v1`

## Mission

Build leak-safe, price-free historical examples from **every eligible settled event**, not only legacy Robber candidates.

Flow:
```
settled event
    ↓
Forebet participant probabilities
    ↓
price-free favorite/underdog identity (identify_forebet_underdog)
    ↓
prior-only pre-event evidence (HistoryIndex, date < current)
    ↓
price-free feature snapshot (ALLOWED only)
    ↓
UNDERDOG_WIN label (label_underdog_outcome, SPORTS registry)
```

Never flows through: legacy odds-first candidate, displayed odds, market implied prob, price availability, legacy Robber score, ROI gate.

## Example Contract

`PriceFreeUnderdogExample` in `src/slumdog/dataset.py`

Required fields:
```
event_id
sport
event_date
favorite_index (1/2)
underdog_index (1/2, never 0)
favorite_probability (0-1)
underdog_probability (0-1)
draw_probability (None or 0-1, context only)
probability_gap
label (0 or 1)
features (dict, ALLOWED only, None preserved for missing)
missingness (dict, 1 missing 0 present)
source_url
raw_sha256
feature_contract_version
label_contract_version
```

Optional audit:
```
exclusion_reason
legacy_provenance_missing (bool)
```

Do NOT include: odds_1, odds_2, price, overround, fair_market_probability, value_edge, ROI, legacy_robber_score, period_values, score_1/2, period_scores, extra-time/penalty, disposition, live_score, result text.

Eligible example must have label 0 or 1. Excluded events recorded in receipt, not disguised as eligible.

## Allowed Features (Minimal Safe Set)

Per `docs/FEATURE_TIMING_CONTRACT.md` ALLOWED.

**Identity (always present):**
- forebet_favorite_probability
- forebet_underdog_probability
- forebet_probability_gap
- forebet_draw_probability (None allowed, context only)
- forebet_draw_probability_missing (1 if None else 0)

**Prior-history (computed strictly from events with date < current, same-date excluded):**
- underdog_prior_games (0 genuine zero if no history)
- favorite_prior_games
- underdog_prior_win_rate (None if no games)
- favorite_prior_win_rate
- recent_win_rate_gap (None if either missing)
- h2h_prior_games (0 genuine zero)
- h2h_underdog_win_rate (None if no H2H)
- h2h_draw_rate (None if no H2H)
- underdog_prior_draw_rate (from prior rows, None if no prior)
- favorite_prior_draw_rate
- prior_scoring_rate_gap (None if no scores)
- prior_conceding_rate_gap

Subset reliably supported by `HistoryIndex` (history.py):
- HistoryIndex._earlier uses bisect_left on (event_date, "") — same-date excluded
- prior_rows(sport, date) returns only earlier dates
- context() gives H2HStats.total_games, wins, period rates, RecentForm wins/games/win_rate
- Extended scoring/draw rates computed from prior_rows where scores available

If HistoryIndex cannot distinguish no history from zero games: documented limitation — games=0 means no history, win_rate=None with missing=1, games missing=0 (genuine zero prior games). Do not fabricate distinction.

## Prohibited Features (First Version)

Regardless of legacy use:
- all odds and price fields (odds_1, odds_2, odds_draw, am variants, best_odd_*, haodd, lscrsp, displayed_odds, implied_probability, price_available)
- overround, fair implied probability, value edge
- legacy Robber score, raw confidence
- period_values (UNKNOWN timing, prohibited per Milestone 3)
- final scores, period scores, penalty scores, extra-time scores, disposition, settlement fields, live score, result text
- unknown-timing text trends (trend_en, trend_raw), unknown-timing detail fields (shots, possession, passes, attacks, etc.) — PARKED, may be added only in later feature-contract version with retained evidence

## Missingness Policy

- Preserve None in features dict for missing
- Add corresponding missingness field: 1 missing, 0 present
- Do not convert missing evidence to meaningful zero
- Do not impute during example construction
- Leave imputation to later approved model pipeline

Example:
```
h2h_prior_games = None
h2h_prior_games_missing = 1
```
Genuine zero:
```
h2h_prior_games = 0
h2h_prior_games_missing = 0
```

Implementation: `dataset.py` builds missingness dict sorted keys, features dict sorted keys, deterministic ordering.

## Timing Guarantees

- Rule: `history_event_date < current_event_date` — enforced via `HistoryIndex._earlier`
- Same-date events do not inform each other (safe default, date-strict exclusion) — `_earlier` uses bisect_left on (event_date, "") so same date excluded
- Future rows cannot affect past — HistoryIndex built from all rows but query filters earlier only
- Input order does not change output — sorted by (event_date, sport, event_id) before build
- H2H only uses prior-date meetings — via `by_pair` earlier filter
- Prior history for one sport cannot enter another sport — `by_sport` and `by_participant` keyed by sport

Tests covering timing in `tests/test_price_free_dataset.py`:
- Future event does not affect earlier example
- Same-date event does not affect another same-date example
- Earlier event affects later-date example
- Input order does not change output
- Adding future row does not change prior examples
- H2H only uses prior-date meetings
- Prior history for one sport cannot enter another

## Eligibility Rules

Eligible only when:
- sport known (SPORTS registry)
- disposition settled and supported (VOID excluded)
- no source conflict
- participant probabilities produce eligible identity (equal/missing/non-finite/out-of-range excluded)
- outcome can be labeled under sport contract (two-way draw excluded, invalid winner excluded)
- required identity features valid

Draw-capable (football, handball, cricket, esoccer):
```
underdog win → label 1
favorite win → label 0
draw → label 0
void → excluded
```

Two-way (basketball, tennis, hockey, baseball, american_football, rugby, volleyball, mma, esports):
```
underdog win → label 1
favorite win → label 0
draw → excluded (UNEXPECTED_DRAW_FOR_TWO_WAY)
void/no-contest → excluded
```

Identity:
```
equal probabilities → excluded
missing probabilities → excluded
non-finite → excluded
out-of-range → excluded
```

Odds availability has no effect on any eligibility decision — price-independence tests.

## Receipt Accounting

`PriceFreeDatasetReceipt` in `dataset.py` — deterministic.

Required counts globally and per sport:
```
input_rows
eligible_examples
positive_underdog_wins
negative_favorite_wins
negative_draws
excluded_void
excluded_source_conflict
excluded_equal_probability
excluded_missing_probability
excluded_non_finite_probability
excluded_out_of_range_probability
excluded_unknown_sport
excluded_unexpected_two_way_draw
excluded_invalid_winner
provenance_present
provenance_missing
```

Also:
```
positive_rate
date_min
date_max
feature_contract_version
label_contract_version
input_digest (sha256 of sorted event_id|sport|date|winner)
per_sport breakdown
```

Invariant:
```
input_rows = eligible_examples + sum(all exclusion categories)
```

Test `test_receipt_accounting_balances` proves this.

Do not report ROI or price coverage as dataset-readiness metrics.

## Deterministic Output

- Stable event ordering: sorted by (event_date, sport, event_id)
- Stable feature-key ordering: sorted keys in to_dict()
- Stable receipt ordering: per_sport sorted
- Duplicate exact composite keys collapse per existing integrity contract (byte-identical winner/probs/scores/participants/disposition)
- Conflicting composite keys fail loudly (ValueError)
- No dependence on dict insertion order or input row order

Do not automatically rewrite source ledgers.

## Price-Independence Tests

In `tests/test_price_free_dataset.py`:
- Odds present vs absent identical identity
- Odds present vs absent identical features
- Odds present vs absent identical label
- Odds present vs absent identical eligibility
- Extreme odds do not alter example
- Reversed odds do not alter favorite/underdog

## Remaining Blockers

- Detail facets still UNKNOWN/PARKED per FEATURE_TIMING_CONTRACT.md — need Jina probes for shots, passes, possession, attacks, etc. before adding to feature contract v2
- period_values remains UNKNOWN PROHIBITED — same selector used pre-event and settlement, no retained bytes prove upcoming population
- Training remains frozen — no LogisticRegression, no MODEL_TRAINING_ALLOWED unlock, no model registry approval, no production pipeline change until dataset contract approved
- No integration into legacy training.py yet (compatibility boundary explicit)

## Verification

```bash
python -m pytest -q
python -m pytest -q tests/test_price_free.py tests/test_price_free_dataset.py
python3 -m py_compile src/slumdog/*.py tests/*.py
python -m pyflakes src/slumdog/dataset.py src/slumdog/underdog.py
git diff --check
git status --short
```

## Codespace Command

Read-only, no network, writes under /tmp, prints receipt summary only:

```bash
python - << 'PY'
from pathlib import Path
import json, gzip, sys
from slumdog.dataset import build_price_free_examples
from slumdog.contracts import SettledEvent

# Try multiple historical sources without altering ledgers
candidates = []
root = Path(".")
# Source 1: data/interim/settled_history.json (if exists)
p1 = root / "data" / "interim" / "settled_history.json"
if p1.exists():
    try:
        payload = json.loads(p1.read_text())
        for item in payload:
            try:
                candidates.append(SettledEvent(
                    event_id=item.get("event_id",""),
                    sport=item.get("sport",""),
                    event_date=item.get("event_date",""),
                    participant_1=item.get("participant_1",""),
                    participant_2=item.get("participant_2",""),
                    winner_index=item.get("winner_index",0),
                    score_1=item.get("score_1"),
                    score_2=item.get("score_2"),
                    probability_1=item.get("probability_1"),
                    probability_2=item.get("probability_2"),
                    draw_probability=item.get("draw_probability"),
                    forebet_pick=item.get("forebet_pick"),
                    odds_1=item.get("odds_1"),
                    odds_2=item.get("odds_2"),
                    league=item.get("league",""),
                    period_scores_1=tuple(item.get("period_scores_1",())),
                    period_scores_2=tuple(item.get("period_scores_2",())),
                    source_url=item.get("source_url",""),
                    disposition=item.get("disposition","SETTLED"),
                ))
            except Exception:
                continue
    except Exception as e:
        print(f"warn: failed to read {p1}: {e}", file=sys.stderr)

# Source 2: data/reports/history_*.jsonl.gz (rolling ledgers)
for gz_path in sorted((root / "data" / "reports").glob("history_*.jsonl.gz")):
    try:
        sport = gz_path.name[len("history_"):-len(".jsonl.gz")]
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                    # history ledger format may differ — try to parse as SettledEvent-like
                    candidates.append(SettledEvent(
                        event_id=item.get("event_id", f"{sport}:{item.get('id','')}"),
                        sport=item.get("sport", sport),
                        event_date=item.get("event_date", item.get("date","")),
                        participant_1=item.get("participant_1", item.get("host","")),
                        participant_2=item.get("participant_2", item.get("guest","")),
                        winner_index=item.get("winner_index", 1),
                        score_1=item.get("score_1"),
                        score_2=item.get("score_2"),
                        probability_1=item.get("probability_1", item.get("prob1")),
                        probability_2=item.get("probability_2", item.get("prob2")),
                        draw_probability=item.get("draw_probability"),
                        forebet_pick=item.get("forebet_pick"),
                        odds_1=item.get("odds_1"),
                        odds_2=item.get("odds_2"),
                        league=item.get("league",""),
                        source_url=item.get("source_url",""),
                        disposition=item.get("disposition","SETTLED"),
                    ))
                except Exception:
                    continue
    except Exception as e:
        print(f"warn: failed to read {gz_path}: {e}", file=sys.stderr)

if not candidates:
    print("No historical ledgers found under data/interim/settled_history.json or data/reports/history_*.jsonl.gz — run depth-sweep/backfill-sport first, or test with synthetic data.")
    sys.exit(0)

examples, receipt = build_price_free_examples(candidates)

# Write output under /tmp (non-tracked)
out_dir = Path("/tmp/slumdog_price_free")
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "receipt.json").write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True))
print(f"Wrote receipt to {out_dir / 'receipt.json'}")
print(f"Examples: {len(examples)} (not dumped)")
print(json.dumps(receipt.to_dict(), indent=2, sort_keys=True))

# Optional: write first 5 examples for inspection (not full dump)
(out_dir / "examples_sample.json").write_text(json.dumps([e.to_dict() for e in examples[:5]], indent=2, sort_keys=True))
print(f"Wrote sample to {out_dir / 'examples_sample.json'}")
PY
```

This command does not alter ledgers, does not fetch network, prints only receipt summary initially, avoids dumping hundreds of thousands of examples into chat.
