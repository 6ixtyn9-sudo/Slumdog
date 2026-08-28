# Milestone 7 — Shadow Picks (Implemented Design)

**Status:** IMPLEMENTED on session branch `arena/01a048de-slumdog` of 2026-08-28.
**Author:** Slumdog agent, session `arena/01a048de-slumdog`
**Base commit:** `b87784fdb590c17b55d4fa1c2bd6c3275dce0f6d` (Milestone 6B merged, PR #10)
**Scope:** A pre-event shadow pick evaluator that consumes an already-captured
Forebet snapshot, applies the **frozen R2 eligibility rule** (read from the
existing 6B frozen config, never duplicated), applies **R1 ranking**, and
emits an immutable per-sport-day payload + manifest. The shadow evaluator is
**read-only** with respect to existing data: it never rewrites raw captures,
capture receipts, settled history, interim events files, or any other
write-once artifact. No network access occurs. No first real run has been
executed; this milestone delivered the local/synthetic path only.

---

## 1. Permanent Rules (Re-Anchored)

All permanent rules from `AGENTS.md`, `HANDOFF.md`, `docs/STATE.md`, and the
frozen 6B config carry forward verbatim. The 6B frozen config SHA-256 is
`666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1` and is
verified at every shadow-evaluator load.

**Additional M7-specific invariants** (each one enforced by code + tested):

1. The shadow pick is a **research artifact**, not a recommendation. The
   label `SHADOW` is permanent on every line. The `select_status` field of
   every selection is `PRIMARY_SHADOW_SELECTION`, `TOP3_EVALUATION_COHORT`, or
   `ELIGIBLE_RANKED_BEYOND_TOP3`. No `CERTIFIED`, no `REJECTED`, no
   `SHADOW_PRICED`, no wagering pathway.
2. Outcome information must never affect pre-event ranking. The typed
   `PreEventRecord` carries no `score_*`, `winner_index`, `disposition`,
   `period_scores_*`, or `odds_*` fields by construction; converting an
   `EventSnapshot` to a `PreEventRecord` drops these.
3. The frozen R2 eligibility rule is read from
   `config/research_baselines_v1.json:rules.R2_CONSERVATIVE_FIXED_RULE` at
   every load; the declaration's `anti_tuning.rule_source_frozen` is verified
   to equal `R2_CONSERVATIVE_FIXED_RULE`. No threshold optimization, no
   result-driven amendment.
4. The 24h pre-event timing gate is **frozen in the declaration** and
   cannot be changed without a new declaration + SHA-256 re-verification.
   The gate is described in the manifest as a conservative pre-event
   timing gate; it is **NOT** proof of exact kickoff time.
5. No global cap on the number of `PRIMARY_SHADOW_SELECTION` per day;
   exactly one primary per `(sport, event_date)`, plus a top-3 cohort of
   ranks 2 and 3 per sport-day, plus eligible-r4+ recorded for audit.
6. The artifact is durable within the local Codespace only; the manifest
   records `durability_status = LOCAL_CODESPACE_ONLY_NOT_BACKED_UP`. A
   second-copy procedure is **not** part of M7 and is deferred to a
   separately approved PR.
7. The implementation is **idempotent and immutable** at the artifact
   level: a second run with the same `input_digest` and same second-precision
   `decision_committed_at` collides on the same `run_id` and is rejected
   with `ArtifactExistsError`. Partial runs (manifest not yet written) are
   preserved untouched for forensic inspection.

---

## 2. Files Added / Modified (this milestone)

| File | Status | Notes |
|---|---|---|
| `src/slumdog/dataset.py` | MODIFIED | Mechanical Design B extraction: new pure helper `build_pre_event_features(sport, event_date, participant_1, participant_2, identity, history)` after `_prior_scoring_stats`. `build_price_free_examples` now calls the helper. 17 ALLOWED_FEATURES keys preserved in identical order. 426 existing research tests remain green. |
| `src/slumdog/shadow_evaluator.py` | NEW | The forward evaluator. ~1100 lines. Owns declaration + frozen-config + timing + identity + R2 eligibility + R1 ranking + atomic artifact emission. Imports `build_pre_event_features` and `HistoryIndex`. Does **not** import `pipeline`, `forebet`, `settlement`, `training`, `research_dataset`, `dataset_audit`, `cli`, or any legacy Robber/Ticket path. |
| `config/shadow_evaluator_v1.json` | NEW | Shadow evaluator declaration. canonical SHA-256 over UTF-8 sorted keys = `dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597` (recorded in this plan, not a config field). |
| `tests/test_shadow_evaluator.py` | NEW | 60 focused tests across 13 contract groups. All behavioral, no AST/repo-wide source scans. |
| `docs/MILESTONE7_SHADOW_PICKS_PLAN.md` | REPLACED | This document — final implemented design, replacing the earlier proposed plan. |
| `HANDOFF.md` | UPDATED | M7 section appended. |
| `docs/STATE.md` | UPDATED | M7 row + verification numbers. |

No other source file in `src/slumdog/` was modified. No CLI subcommand was
added. No scheduler / cron / GitHub Action was added. No raw capture or new
fixture was added.

---

## 3. Frozen Rule Source (no duplication)

The shadow evaluator **does not** carry a copy of the R2 eligibility
thresholds. It loads `config/research_baselines_v1.json` via
`load_frozen_baseline_config(root)` which:

- reads the file,
- recomputes the canonical SHA-256 (sorted keys, compact separators, UTF-8),
- asserts the result equals
  `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`,
- asserts the rule structure
  (`policy_candidate=False`, `quota_forced=False`,
  `rank="R1_ALWAYS_RANK_COMPARATOR"`),
- asserts the eligibility tuples
  `(underdog_prior_games, gte, 5)`,
  `(favorite_prior_games, gte, 5)`,
  `(h2h_prior_games, gte, 1)`,
  `(forebet_probability_gap, lte, 0.2)`.

Any drift in the frozen 6B config raises `FrozenConfigHashMismatch` and
the evaluator refuses to read input or write output. The declaration also
embeds `anti_tuning.rule_source_frozen_config_sha256` and is cross-checked
at load.

---

## 4. Declaration (`config/shadow_evaluator_v1.json`)

The declaration is the auditable contract for a single M7 run. Its
top-level keys and their `load_shadow_declaration` invariants:

- `declaration_version = "shadow_evaluator_v1"` (frozen).
- `frozen_at` — authoring date (informational).
- `authoring_session`, `authoring_base_commit` — informational.
- `authorizations`:
  - `shadow_evaluation_authorized = true` (must be true)
  - `production_authorized = false` (must be false)
  - `shortlist_policy_authorized = false` (must be false)
  - `training_authorized = false` (must be false)
  - `threshold_optimization_authorized = false` (must be false)
- `anti_tuning`:
  - `result_driven_amendments = "prohibited"`
  - `tuning_on_observed_results = "prohibited"`
  - `rule_source_frozen = "R2_CONSERVATIVE_FIXED_RULE"`
  - `rule_source_frozen_config_sha256 = "666dabe7...d700a1"`
- `timing_safety`:
  - `safe_cutoff_offset_hours_utc = 24` (frozen)
  - `safe_cutoff_anchor = "target_date_00_00_UTC"`
  - `require_captured_at_present = true`
  - `require_decision_committed_at_present = true`
  - `require_both_timestamps_tz_aware_utc = true`
  - `fail_closed_status_on_violation = "PRE_EVENT_TIMING_UNVERIFIED"`
  - `margin_frozen_in_declaration = true`
  - `margin_description` — explicitly states "conservative pre-event
    timing gate; not proof of exact kickoff time".
- `rule`:
  - `name = "R2_CONSERVATIVE_FIXED_RULE"`
  - `policy_candidate = false`
  - `quota_forced = false`
  - `rank_policy = "R1_ALWAYS_RANK_COMPARATOR"`
  - `eligibility_features` — informational; the authoritative values come
    from the frozen 6B config.
- `cohort_policy`:
  - `primary_selection_per_sport_day = 1`
  - `top3_cohort_per_sport_day = 2`
  - `no_global_cap = true`
  - `ranks_4_plus_recorded_but_not_in_cohort = true`
- `history_loader`:
  - `name = "v2_loaded_priors_only"`
  - `use_loaded_v2_history = true`
  - `strict_event_date_lt_target = true`
  - `exclude_same_day = true`
  - `disallow_void = true`, `disallow_no_contest = true`,
    `disallow_two_way_anomalous_draw = true`
  - `deterministic_duplicate_handling = "deterministic"`
  - `conflict_handling = "report_all_conflicting_source_hashes"`
  - `odds_must_not_influence = true`
- `capture_provenance`:
  - `require_raw_capture_receipt = true`
  - `require_per_sidecar_hash_verification = true`
  - `hash_algorithm = "sha256"`
  - `preserve_sidecar_path`, `body_path`, `captured_at`, `source_url`,
    `route` — all `true`
  - `never_rewrite_raw_capture`, `never_rewrite_capture_receipt`,
    `never_rewrite_interim` — all `true`
- `idempotency`:
  - `run_id_unique_per_finalization = true`
  - `input_digest_canonical_inputs_only = true`
  - `decision_digest_canonical_selections_only = true`
  - `payload_file_sha256_after_final_write = true`
  - `no_overwrite = true`, `no_force = true`, `no_supersede = true`
  - `partial_run_preserved_untouched = true`
  - `retry_creates_new_run_id_sibling_dir = true`
  - `manifest_written_last = true`
- `artifact_path`:
  - `root = "data/reports/shadow"`
  - `per_date = "data/reports/shadow/<target_date>/"`
  - `per_run = "data/reports/shadow/<target_date>/<run_id>/"`
  - `manifest = "manifest.json"`, `payload = "shadow_selections.json"`,
    `summary_markdown = "summary.md"`
- `durability`:
  - `status = "LOCAL_CODESPACE_ONLY_NOT_BACKED_UP"`
  - `second_copy_procedure = "DEFERRED_TO_SEPARATE_APPROVAL"`
  - `no_compact_digest_writer_in_v1 = true`
  - `no_external_storage_in_v1 = true`
  - `no_force_overwrite_in_v1 = true`
  - `git_tracked_exception = false`
- `statuses`:
  - `primary_selection = "PRIMARY_SHADOW_SELECTION"`
  - `top3_cohort = "TOP3_EVALUATION_COHORT"`
  - `no_selection_sport_day = "SHADOW_NO_SELECTION"`
  - `run_blocked = "SHADOW_RUN_BLOCKED"`
  - `timing_unverified = "PRE_EVENT_TIMING_UNVERIFIED"`
  - `feature_incomplete = "FEATURE_INCOMPLETE_OR_R2_INELIGIBLE"`

---

## 5. Typed `PreEventRecord` Boundary

`PreEventRecord` is a frozen dataclass with **15** fields, all required
for identity, feature construction, ranking, and provenance:

`event_id`, `sport`, `event_date`, `participant_1`, `participant_2`,
`probability_1`, `probability_2`, `draw_probability`, `source_url`,
`raw_sha256`, `captured_at`, `body_path`, `route`, `facets`, `facet_timing`.

`__post_init__` enforces:

- `event_id` non-empty
- `sport` in `SPORTS` (i.e. one of 14 registered sports)
- both participants non-empty
- participants are not a self-pair (case-insensitive canonical key)
- `captured_at` and `event_date` non-empty
- structural field-name check: none of
  `score_1, score_2, winner_index, disposition, period_scores_1,
   period_scores_2, extra_time_score, penalty_score, odds_1, odds_2,
   price, overround, implied_probability, live_score, result, result_text`
  is a dataclass field.

`PreEventRecord.from_event_snapshot(snap, *, body_path="")` is the only
adapter. It accepts an `EventSnapshot` (which carries odds / score /
predicted_score / etc.) and copies **only** approved fields. Facets are
filtered to those with explicit `facet_timing` keys so the record carries
pre-event evidence by construction.

The dataclass cannot accept the prohibited fields by name; an attempt to
construct one with a `score_1=...` keyword argument fails with
`TypeError: __init__() got an unexpected keyword argument 'score_1'`.

---

## 6. Frozen Timing Gate

The gate is implemented by `safe_cutoff_utc(target_date)` and
`_parse_utc(timestamp)`:

- `safe_cutoff_utc(target_date)` returns
  `target_date 00:00 UTC − 24h`. For `target_date="2026-08-28"` it returns
  `2026-08-27T00:00:00+00:00`. The function rejects malformed
  `target_date` values with `ValueError`.
- `_parse_utc(ts)` requires a strict ISO 8601 with explicit `Z` or `±HH:MM`
  offset; naive timestamps and non-ISO strings raise `ValueError`.

The gate is enforced twice in `run_shadow_evaluation`:

1. **Per-record `captured_at`** — if `_parse_utc(record.captured_at)`
   raises or `> safe_cutoff`, the record is counted as `timing_rejected`
   with status `PRE_EVENT_TIMING_UNVERIFIED` and never enters
   `selections`.
2. **Run-level `decision_committed_at`** — captured at the very start of
   `run_shadow_evaluation` from `datetime.now(timezone.utc)`. The
   declaration requires it to be ≤ safe_cutoff; if not, the run is
   blocked. (In the synthetic/local path this is also tested.)

The boundary at exactly `safe_cutoff_utc` is **inclusive** (`≤`, not `<`),
and a dedicated test asserts that a record with
`captured_at="2026-08-27T00:00:00Z"` is accepted while one with
`captured_at="2026-08-27T00:00:01Z"` is rejected.

---

## 7. Identity Validation

`validate_event_identity(record)` returns `(ok, reason)` and rejects:

- empty participants
- self-pairs (canonical-key comparison)
- unknown sport (not in the 14-sport `SPORTS` registry)
- non-zero `draw_probability` for a two-way sport (esports-style capture
  corruption signal)
- missing `probability_1` or `probability_2`
- out-of-range `[0,1]` probability
- ambiguous underdog (equal participant probabilities, identity
  ineligible — the `identify_forebet_underdog` reason is propagated)
- `event_id` reuse with conflicting participants is documented as a
  boundary test for the parse layer (in scope of the parse-level
  staging). For the forward evaluator's typed boundary, the
  `PreEventRecord` is constructed per-event so identity-level conflicts
  are not possible inside one evaluation.

---

## 8. R2 Eligibility (frozen, read-only)

`is_r2_eligible(features)` returns `(eligible, reason)` and applies the
exact R2 thresholds read from the frozen config:

- `underdog_prior_games >= 5`
- `favorite_prior_games >= 5`
- `h2h_prior_games >= 1`
- `forebet_probability_gap <= 0.2`

`None` is treated as missing. The function reads **only** those four
feature keys and applies no other logic. Boundary tests assert the exact
behavior at 4 vs 5, 0 vs 1, 0.2 vs 0.200001, None vs 0.

---

## 9. R1 Ranking (frozen comparator)

`r1_sort_key(features, event_id)` returns a tuple suitable for
`list.sort(key=...)`. The precedence is:

1. `recent_win_rate_gap` **DESC** (None sorts last)
2. `h2h_underdog_win_rate` **DESC** (None sorts last)
3. `forebet_probability_gap` **ASC**
4. `event_id` **ASC** (deterministic tie-break)

This is the same `R1_ALWAYS_RANK_COMPARATOR` that the 6B analyzer uses
in `baseline_analyzer.r1_sort_key` (lines 333, comparator identity).
The shadow evaluator does not invent a new ranking.

---

## 10. Per-Sport-Day Cohort Semantics

After R2 eligibility and R1 ranking, each `(sport, event_date)` group is
sorted by `r1_sort_key`. Statuses are assigned:

- `rank 1` → `PRIMARY_SHADOW_SELECTION`
- `rank 2..3` → `TOP3_EVALUATION_COHORT`
- `rank 4+` → `ELIGIBLE_RANKED_BEYOND_TOP3`

Sport-days with **zero** eligible events get
`status = "SHADOW_NO_SELECTION"` in the `sport_day_summary`, with
`eligible_count = 0`, `primary = null`, `cohort = []`, and
`eligible_r4_plus = []`. Sport-days with **at least one** eligible event
get `status = "SHADOW_RULE_QUALIFIED"`.

No global cap. Multiple sport-days, each with its own primary + cohort,
are allowed and tested.

---

## 11. 4-ID Split and Atomic Write

The run's identity is a 4-tuple:

- `run_id` — `sha256(canonical_json({"version": "shadow_evaluator_v1",
  "input_digest": ..., "decision_digest": ..., "decision_committed_at": ...}))[:16]`.
  Computed **after** `input_digest` and `decision_digest`.
- `input_digest` — `sha256(canonical_json({"declaration_sha256": ...,
  "target_date": ..., "records": sorted_tuples}))`. Includes sport, event_id,
  event_date, both participants, all three probabilities, raw_sha256, and
  captured_at for each input record.
- `decision_digest` — `sha256(canonical_json({"selections":
  sorted_without_run_id, "accounting": ...}))`. `run_id` is stripped
  from each selection before hashing to avoid self-reference.
- `decision_committed_at` — ISO 8601 UTC at the very start of
  `run_shadow_evaluation`, truncated to seconds, suffixed with `Z`.

Self-reference resolution ordering:

1. Build the selection list (no `run_id`).
2. Compute `decision_digest`.
3. Compute `input_digest` (no dependency on `run_id`).
4. Compute `run_id` from `input_digest` + `decision_digest` +
   `decision_committed_at`.
5. Insert `run_id` into each selection.
6. Build the final payload and the manifest.
7. Compute `payload_file_sha256` from the on-disk payload file (after
   `os.replace`).
8. Write the manifest last (after the payload), also via temp + `os.replace`.
9. Refuse to overwrite any pre-existing `artifact_dir`; raise
   `ArtifactExistsError`.

If the manifest finalization fails after the payload is written, the
artifact directory exists with `shadow_selections.json` but **no**
`manifest.json`. The test
`test_blocked_run_artifact_missing` injects exactly this failure and
asserts the post-condition.

---

## 12. Staged Accounting Equations

The manifest records three stages of mutually exclusive accounting.

**Capture-level** (sums to `raw_capture_receipt_entries`):

```
captures_verified + captures_missing + captures_hash_mismatch
    + captures_schema_invalid + captures_parse_failed
    + captures_unsupported = raw_capture_receipt_entries
```

**Parse-level** (sums to `parsed_rows_total`):

```
unique_rows + exact_duplicate_rows + conflicting_rows
    + malformed_rows = parsed_rows_total
```

**Decision-level** (sums to `unique_nonconflicting_rows`):

```
timing_rejected + identity_ineligible
    + feature_incomplete_or_r2_ineligible
    + primary_selected
    + top3_cohort_selected
    + eligible_ranked_beyond_top3
    = unique_nonconflicting_rows
```

In the forward-only path, `captures_verified = unique_rows = 1× record
count` because the upstream capture / parse boundary is the operator's
responsibility. The forward evaluator asserts all three equations before
writing the manifest.

---

## 13. Per-Sport Kickoff Matrix

`forebet.py:330-337` shows the matrix:

- 12 of 14 sports accept future-dated `target_date` via the
  `/en/<sport.path>/predictions/<target_date>` HTML route.
- Football uses the `in=<target_date>` JSON endpoint
  (`forebet.py:330`).
- 2 sports are `current_only=True` in `sports.py`:
  - `esoccer` (line 101)
  - `afl` (line 107)

`EventSnapshot.kickoff` is a calendar-date display string
(`parsers.py:215` for football, `parsers.py:411` for tennis-style HTML
parses), **not** a wall-clock timestamp. The frozen 24h timing gate is
therefore documented as a **conservative** pre-event gate; it is not
proof of exact kickoff time.

The forward evaluator does not currently use the per-sport
`current_only` flag to suppress a sport — that is a future hardening
item. The first real run will have to address it explicitly.

---

## 14. No-First-Real-Run Confirmation

- The M7 implementation was authored entirely in the Arena workspace.
- No real Forebet network request was made.
- No real shadow evaluation run was performed against a live capture.
- The M7 implementation was verified against **synthetic** input only:
  - `tests/test_shadow_evaluator.py` constructs `PreEventRecord` and
    `SettledEvent` instances in memory and runs the evaluator end-to-end
    against an in-memory `HistoryIndex`.
  - Each test uses a per-test `tempfile.TemporaryDirectory()` rooted at
    `tmp_root`, so artifacts are written under `/tmp/tmpXXXXXXX/data/...`
    and never at the real `data/reports/shadow/...` path.
  - A monkey-patched `urllib.request.urlopen` (test
    `test_no_network_access`) asserts no network call is made.
  - A monkey-patched set of forbidden entry points (test
    `test_no_calls_to_pipeline_or_collectors`) asserts the evaluator
    does not import or call `pipeline.build_shadow_robbers`,
    `pipeline.parse_capture_receipt`, `forebet.ForebetCollector`, or
    `settlement.append_settled_from_capture`.

---

## 15. Unresolved Items Before First Genuine Forward Run

1. **Real-Codespace verification of `data/` durability.** The evaluator
   marks `durability_status = LOCAL_CODESPACE_ONLY_NOT_BACKED_UP`. A
   second-copy procedure preserving the full canonical payload must be
   separately approved before claiming permanent preservation across
   Codespace deletion.
2. **`current_only` sports (esoccer, afl).** The forward evaluator does
   not currently suppress these. The first real run must either filter
   them out at the source (Forebet does not produce a `target_date`
   capture for them) or add an explicit pre-filter inside the
   evaluator.
3. **Capture-level and parse-level staging fields in the manifest.**
   The forward evaluator populates these with degenerate values
   (`captures_verified=1×count`, `unique_rows=1×count`,
   `exact_duplicate_rows=0`, `conflicting_rows=0`, `malformed_rows=0`).
   A separate capture-parsing pipeline (not in M7) will own the
   pre-evaluator staging. The forward evaluator's manifest schema is
   intentionally compatible with that future work.
4. **Compact-digest writer / external storage.** Explicitly deferred
   (`no_compact_digest_writer_in_v1`, `no_external_storage_in_v1`).
5. **Real-data SHA-256 manifest of every history input.** The
   declaration's `history_loader` block requires this, but the
   in-memory `HistoryIndex(settled_history)` constructor used by
   `evaluate_from_records` does not currently record per-input SHA-256
   into the manifest. The first real run will need a thin adapter that
   records every input path + SHA-256 in the manifest, or a separate
   history-loader wrapper that does.
6. **Recorded-against-decision-digest conflict detection.** The
   declaration's `history_loader.conflict_handling` requires reporting
   every conflicting source hash. The current `HistoryIndex` does not
   expose this; the M7 evaluator trusts the supplied `settled_history`
   list. The first real run will need a separate history loader that
   performs the same Milestone 6A v2 validity checks and reports
   conflicts.

---

## 16. Test Inventory (60 tests in 13 contract groups)

The 60 tests in `tests/test_shadow_evaluator.py`:

- **Group 1 — declaration and frozen-config integrity (5 tests):**
  declaration loads, frozen config SHA-256 matches, frozen R2 rule
  structure verified, frozen config drift rejected, frozen R2
  eligibility drift rejected.
- **Group 2 — authorization gates fail closed (5 tests):**
  `production_authorized=true` rejected, `shortlist_policy=true`
  rejected, `training_authorized=true` rejected,
  `threshold_optimization=true` rejected, `shadow_evaluation=false`
  rejected.
- **Group 3 — shared feature extraction (1 test):**
  `build_pre_event_features` output matches the inline
  `build_price_free_examples` output.
- **Group 4 — strict prior-history cutoff and v2 validity (2 tests):**
  same-day exclusion, VOID disposition handling boundary.
- **Group 5 — raw-capture hash and provenance verification (3 tests):**
  sidecar hash ok, hash mismatch, evaluate preserves source provenance.
- **Group 6 — staged accounting (3 tests):** single-eligible balanced,
  top-3 cohort, r4-plus.
- **Group 7 — R2 eligibility boundary parity (12 tests, parametrized):**
  underdog_prior_games, favorite_prior_games, h2h_prior_games,
  forebet_probability_gap, each with boundary values 4 vs 5, 0 vs 1,
  0.2 vs 0.200001, etc.
- **Group 8 — R1 ranking and tie-break (3 tests):** sort key order,
  None sorts last, gap ascending.
- **Group 9 — primary / top-3 cohort semantics (2 tests):** no global
  cap, zero-eligible sport-day.
- **Group 10 — no global cap (1 test):** two sport-days, two primaries.
- **Group 11 — conservative timing cutoff (5 tests):** after-cutoff
  rejected, exact boundary accepted, well-before-cutoff accepted,
  `safe_cutoff_utc` returns correct value, `_parse_utc` requires tz.
- **Group 12 — odds and outcomes cannot influence (3 tests):** odds
  change does not change decision, typed record has no forbidden
  fields, `from_event_snapshot` drops odds and predicted_score.
- **Group 13 — no-selection and blocked-run (2 tests):** sport-day
  summary shows `SHADOW_NO_SELECTION`, blocked finalization leaves
  payload but no manifest.
- **Group 14 — immutable no-overwrite (2 tests):** second run with
  same input raises `ArtifactExistsError`, changing input creates a
  sibling directory.
- **Group 15 — collector / settlement / publisher / production not
  invoked (2 tests):** no network access, no calls to pipeline /
  collectors / settlement.
- **Group 16 — existing full suite remains green:** verified by
  `pytest -q` (426 + 60 = 486 passed).

Plus 9 supplementary tests (validate_event_identity boundary cases,
helpers, etc.).

---

## 17. Verification Receipt

- `python3 -m pytest -q` → 486 passed in ~55s
- `python3 -m py_compile scripts/*.py src/slumdog/*.py tests/*.py` → OK
- `python3 -m pyflakes scripts src/slumdog tests` → only 13 pre-existing
  unused-import warnings in `tests/test_dataset_hardening.py`,
  `tests/test_dataset_final_integrity.py`,
  `tests/test_dataset_conflict_census.py`, and
  `tests/test_research_incremental_builder.py`. **No** new pyflakes
  warnings introduced.
- `git diff --check` → clean.
- Frozen baseline config SHA-256:
  `666dabe7ea21e11867cf4816f4c2edcd771247646c6c9d7726c22611cda700a1`
  matches expected. Recomputed and verified.
- New shadow declaration canonical SHA-256:
  `dd08976a262e7a1882a4e29846612094c20447faf587c01a42608d57f4f4d597`.
- 17 ALLOWED_FEATURES keys preserved in identical order in the
  refactored helper; 426 existing research tests pass.
- No real Forebet network request.
- No real shadow evaluation run.
- No commit / push / PR.
