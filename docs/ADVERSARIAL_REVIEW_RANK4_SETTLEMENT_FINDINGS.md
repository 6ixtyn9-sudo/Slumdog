# Adversarial Review — rank-4+ settlement grading + the Wilson "certification" report

**Reviewer:** fresh agent, session `arena/01a07b8b-slumdog`
**Date:** 2026-09-07
**Base commit inspected:** `edd5ff22bd5ad499247b6dcb97d3aee11e5807d1` (only commit in this clone)
**Mandate:** try to break the prior agent's findings; verify against raw data and the real
code path; do not trust its framing, variable names, or docstrings.
**Scope of changes made by this review: NONE.** No source, config, evidence, or artifact was
modified. This is a findings document only, per the handoff's explicit "do not fix anything
without flagging it to the user first".

---

## 0. BLOCKER — the review target is not in this repository

Before any lead could be audited, one fact invalidates part of the mandate:

| Claimed | Actual in this checkout |
|---|---|
| `src/slumdog/certification.py` added by commit `e720973` | **absent**; commit `e720973` does not exist (`git cat-file -t` → *Not a valid object name*) |
| `HANDOFF_ADVERSARIAL_REVIEW_CERTIFICATION.md` committed as `3051df2` | **absent**; commit `3051df2` does not exist |
| — | `git rev-list --all` returns exactly **one** commit: `edd5ff2` |
| — | `origin/main` tree contains no `certification.py`; `git fetch` fails (`could not read Username for 'https://github.com'` — no credential path in this sandbox) |

**Consequence:** LEAD #4 and LEAD #6 ask for an audit of `certification.py`'s own source
(its `wilson_bounds`/`wilson_lb`/`wilson_ub` implementation, `_classify_tier()`, its glob,
its z constant). **That source cannot be audited here — it does not exist in this clone.**
Rather than declare those leads unactionable, I verified the *mathematics* independently
(§5) and reconstructed the tool's *behaviour* from its published numbers (§5.2), which turned
out to be decisive.

I also could **not run the test suite**: `pytest` is not installed and the environment is
PEP-668 externally managed with no working package index. Per `AGENTS.md` ("Never claim
skipped test passed") I state this plainly: **no pytest run was performed.** All code-level
verification below was done by importing the real modules directly and executing them.
`bs4` (an HTML-parsing dependency irrelevant to grading) was stubbed in-memory to allow
`import slumdog.shadow_settle`; the grading logic itself ran unmodified.

---

## 1. LEAD #1 — CONFIRMED, and materially **worse** than the handoff described

### 1.1 The mechanism, verified in code (not paraphrase)

1. `shadow_evaluator.py` ~L948–965 builds `considered_pool_dicts` with **exactly six keys**:
   `sport`, `event_id`, `event_date`, `considered_status`, `eligible`, `rank_within_sport_day`.
   Verified against real data — all 1366 / 878 / 55 pool entries have precisely this key set.
2. `shadow_settle.py::_build_event_index` (L186–203) admits every pool entry whose
   `considered_status == "ELIGIBLE_RANKED_BEYOND_TOP3"`. (The `if key in index: continue`
   guard correctly gives `selections[]` precedence, so ranks 1–3 are *not* corrupted.)
3. `shadow_settle.py::grade_all_entries` L501: `underdog_index = entry.get("underdog_index", 0)`.
   For pool rows the key is absent, so this **always** yields `0`.
4. `grade_underdog_win` L122: `if winner_index == underdog_index: return SUCCESS`.

### 1.2 Where I **disagree** with the handoff — there is no SUCCESS edge case

The handoff asserts a rank-4+ row could grade SUCCESS "if the actual match result was a draw
AND that sport's `draw_possible` is False". **That is wrong.** `winner_index == 0` is
intercepted at L117–121 and returns `FAILURE` (draw-capable) or `UNRESOLVED` (two-way) — so
L122 is *never reached* with `winner_index == 0`. With `underdog_index == 0`, the equality at
L122 can only fire when `winner_index == 0`, which has already returned.

Exhaustive execution of the real `grade_underdog_win` with `underdog_index=0` over
**768 combinations** (14 registered sports + 2 unregistered/empty × 12 dispositions incl.
`None`/`""`/`POSTPONED`/`WALKOVER` × `winner_index ∈ {0,1,2,None}`):

```
outcomes: {'FAILURE': 376, 'UNRESOLVED': 392}
SUCCESS cases: NONE — structurally unreachable
```

**rank-4+ SUCCESS is mathematically unreachable, not improbable.** The tier is a numerator
hard-wired to zero over a large denominator. Empirically consistent: **all 867** pool rows
across the three settled dates carry `underdog_index == 0`, and **0** grade SUCCESS
(820 FAILURE / 15 UNRESOLVED / 32 UNSETTLED).

### 1.3 Magnitude — the true rate is ~33%, not ~0%

The missing identity is **recoverable from evidence already committed**: each manifest's
`input_provenance.capture_record_tuples` carries
`(sport, event_id, event_date, participant_1, participant_2, probability_1, probability_2,
draw_probability, raw_sha256, captured_at, body_path, source_url)` — 1366 tuples for
2026-09-05, exactly one per pool entry. These are **pre-event** probabilities captured
2026-09-03, before the fixtures were played.

I recomputed identity with the project's own frozen rule
`slumdog.underdog.identify_forebet_underdog` ("higher prob = favorite, lower = underdog;
exact equal → no underdog"), then re-graded with the unmodified `grade_underdog_win`.

**Method validation (essential):** applied to the 9 `selections[]` rows whose true
`underdog_index` *is* committed, the recomputation matched **9/9**.

**Result on the 867 rank-4+ rows:**

| | committed evidence | recomputed from committed pre-event probabilities |
|---|---|---|
| SUCCESS | **0** | **270** |
| FAILURE | 820 | 550 |
| UNRESOLVED | 15 | 15 |
| UNSETTLED | 32 | 32 |
| decided n | 820 | 820 |
| success rate | **0.0000** | **0.3293** |
| Wilson 95% | [0, 0.0048] | **[0.2980, 0.3622]** |

Transition matrix: `FAILURE→SUCCESS: 270`, `FAILURE→FAILURE: 550`, `UNRESOLVED→UNRESOLVED: 15`,
`UNSETTLED` unchanged (respecting the pipeline's own `settled is None → UNSETTLED` guard).

**Independent audit of all 270 flips:** each was re-checked against the raw probabilities and
the real score — the underdog must be the *lower* of `probability_1`/`probability_2` **and**
must equal `winner_index` **and** the scoreline must confirm that participant won.
**0 of 270 failed.** Examples:

```
2026-09-05 football:2418146 São Paulo(0.28) vs Atlético Mineiro(0.32) draw=0.39
           underdog=1 winner=1 score=2-0  -> genuine underdog win, committed as FAILURE
2026-09-02 football:2493529 Avispa Fukuoka(0.39) vs Urawa Red Diamonds(0.29) draw=0.31
           underdog=2 winner=2 score=2-3  -> genuine underdog win, committed as FAILURE
2026-09-02 football:2528729 Kheybar Khorramabad(0.15) vs Foolad Khuzestan(0.31) draw=0.55
           underdog=1 winner=1 score=2-0  -> genuine underdog win, committed as FAILURE
```

**Therefore the report's single "most confident" finding is not a weak signal, and not merely
an uninterpretable artifact — it is inverted in implication.** Rank-4+ underdogs win about a
third of the time (32.9%), which is in the same range as ranks 1–3 (4/9 = 44.4%, Wilson
[0.189, 0.733]). The claim "lower-ranked candidates essentially never produce underdog wins"
is **false**.

### 1.4 Whose bug is it? (handoff question 3) — neither of the two options offered

It is **not** a `certification.py` interpretation bug. `certification.py` faithfully
summarised corrupt input. There are **three** distinct defects, in two other files plus the
test suite:

- **(a) `shadow_evaluator.py` ~L954–963 — omission.** The identity was *in scope and dropped*.
  The rank loop binds `identity = ev["identity"]` and serialises `identity.underdog_index`,
  `favorite_index`, and all probabilities into `selections[]` at L916–921. The pool-dict
  construction in the same function had the identical object available and wrote six fields.
  This is a six-line serialisation omission, not a data-availability limit.
- **(b) `shadow_settle.py` L501 — silent sentinel collision.** `.get("underdog_index", 0)`
  defaults a *missing* field to `0`, which `grade_underdog_win`'s own docstring reserves for
  "draw". A missing-value default must never equal a meaningful sentinel; this should raise
  or mark the row ungradable.
- **(c) `tests/test_shadow_settle.py` ~L194–207 — fixture/schema divergence (root cause of
  invisibility).** The hand-built fixture pool entry contains:

  ```python
  {"sport": "football", "event_id": "football:12347", "event_date": target_date,
   "considered_status": "ELIGIBLE_RANKED_BEYOND_TOP3", "eligible": True,
   "rank_within_sport_day": 4,
   "underdog_index": 2, "underdog_probability": 0.18,      # <-- production NEVER emits these
   "favorite_index": 1, "favorite_probability": 0.62}
  ```

  The test suite therefore validates a schema production does not produce. Demonstrated by
  running the real `_build_event_index` + `grade_underdog_win` on both shapes for the same
  event and the same real result (São Paulo 2–0, true underdog = 1):

  ```
  PRODUCTION manifest -> entry.get('underdog_index',0) = 0   grade(winner=1) = FAILURE
  TEST FIXTURE        -> entry.get('underdog_index',0) = 1   grade(winner=1) = SUCCESS
  ```

  Reinforcing this: `test_r4plus_from_pool` asserts only `r4["_source"] == "considered_pool"`
  — it never asserts a grade. And `tests/test_shadow_evaluator.py` ~L1588–1591 asserts
  `r4plus_in_pool == []` with the comment "*nothing may carry ELIGIBLE_RANKED_BEYOND_TOP3
  while uncapped*" — the evaluator's own tests declare this branch unreachable, while
  committed manifests contain **1317** such entries. The code path was believed dead, so
  nobody fed it a realistic fixture.

### 1.5 Blast radius (handoff question 4) — confined to `ranks_4_plus`

Re-graded all 9 primary/cohort rows using `underdog_index` taken from `shadow_selections.json`
itself (not from the settlement file): **9 unchanged, 0 changed**. Field values matched the
settlement rows in all 9 cases. `primary` and `cohort_ranks_2_3` are **sound**.

### 1.6 Forward-looking — the tier is about to silently die

`config/shadow_evaluator.json` now declares `cohort_policy.top3_cohort_per_sport_day: null`
("UNCAPPED"), and `shadow_evaluator.py` L209–216 validates it. With `cohort_width is None`,
`last_cohort_rank = math.inf`, so `rank_idx > last_cohort_rank` is never true and the
`ELIGIBLE_RANKED_BEYOND_TOP3` branch (L905) is **unreachable**. Verified: no rank in
`1..99999` exceeds `inf`.

Consequences:
- New runs place every R2-eligible ranked candidate in `selections[]` **with** full identity →
  grading correct going forward. The bug stops generating new corrupt rows.
- But `ranks_4_plus` degenerates to **n = 0 for every future date**, while the 1317 historical
  pool entries (867 of them settled) stay mislabelled. A permanently-empty tier is its own
  quiet failure mode: an absent tier is easy to misread as "no data yet" rather than
  "this tier can no longer be produced".
- Note the committed evidence spans a **config regime change**: 2026-09-10…09-13 manifests
  still record `eligible_ranked_beyond_top3` of 20/53/134/195, i.e. they were produced under
  the capped regime. Any longitudinal comparison across 2026-09-07 mixes two different
  cohort policies and is not apples-to-apples.

### 1.7 Two latent defects found incidentally

- `grade_underdog_win(underdog_index=0, winner_index=None, disposition="SETTLED")` returns
  **FAILURE**, not UNRESOLVED/UNSETTLED: `None == 0` is False, so the draw branch is skipped,
  then `None == 0` is False again → falls through to FAILURE. Currently masked by the
  `if settled is None: grade = UNSETTLED` guard at L511, but any `SettledEvent` that exists
  with a null `winner_index` would be silently graded FAILURE. (This also means my *first*
  recomputation pass overstated FAILURE by 32; the table in §1.3 is the corrected accounting.)
- Disposition `SETTLED_CUP` occurs once in real committed data but appears nowhere in
  `grade_underdog_win`'s documented contract. It happens to fall through to normal grading
  (correct behaviour), but it is an undocumented value in a function whose docstring claims
  to enumerate the immutable contract.

---

## 2. LEAD #1.5 — the immutability question, reasoned from the actual definitions

Facts, not assumptions:

- `config/shadow_evaluator.json → durability`: `no_force_overwrite: true`,
  `no_compact_digest_writer: true`, `no_external_storage: true`,
  `status: LOCAL_CODESPACE_ONLY_NOT_BACKED_UP`. Each committed manifest echoes the same as
  `durability_policy`.
- `shadow_settle.py::write_settlement_artifact` (~L698–705) raises `SettlementError`
  "refusing to overwrite existing settlement artifact/marker". Enforced in code **and**
  covered by `test_no_overwrite`.
- `anti_tuning`: `result_driven_amendments: prohibited`, `tuning_on_observed_results: prohibited`,
  `config_hash_immutable_after_first_real_run: true`.
- `AGENTS.md`: "Preserve immutable captures"; "Missing stays missing; never zero-fill";
  "Result, final score, settlement status, post-event facts cannot enter features."

**Reasoning:**

1. `no_force_overwrite` governs the *settlement writer*. It prohibits rewriting an existing
   artifact. It does **not** prohibit creating a new, differently-named derived artifact.
2. Correcting this is **not** a result-driven amendment. The anti-tuning rule forbids changing
   the *rule or config* in response to observed outcomes to flatter the metric. Here the frozen
   contract (`grading_contract`, `grade_underdog_win`, frozen R2 rule,
   `rule_source_frozen_config_sha256 = 666dabe7…`) stays **byte-identical**. What changes is a
   *measurement instrument* that was comparing real winners against a placeholder. Fixing an
   instrument is not tuning a rule.
3. It also does **not** breach "post-event facts cannot enter features" or "never zero-fill":
   the recomputation uses only pre-event probabilities captured 2026-09-03 that are **already
   committed inside the manifest**. Nothing new is imported, and nothing missing is invented —
   the value was never missing, it was present and unread.
4. **Recommended shape (append-only erratum):** leave `settlement.json` and
   `settlement.json.sha256` byte-for-byte untouched; publish a *new* artifact (e.g.
   `settlement_erratum_rank4_v1.json`) that embeds the original artifact's sha256, names the
   defect, and carries the recomputed grades. That honours `no_force_overwrite` in both letter
   and spirit and preserves the audit trail showing what was originally claimed.
5. **Honest caveat:** this was noticed *because* results looked anomalous. That is a legitimate
   way to find an instrument defect, but it draws a hard line — the corrected numbers may be
   used to fix the instrument and to restate the record. They must **not** be used to justify
   any subsequent rule, config, or threshold change. Doing so would be exactly the
   result-driven tuning `anti_tuning` prohibits.
6. The forward code fixes ((a) serialise identity into pool dicts, (b) replace the silent
   `.get(..., 0)` default with a loud failure, (c) rebuild the fixture from the real
   production schema and add a schema-divergence guard) are ordinary bug fixes and are
   permitted — indeed `AGENTS.md` requires "every parser change needs minimal fixture-based
   regression test". `tests/test_adversarial_review.py` is the established precedent in this
   repo for housing regressions born of an adversarial review; notably it covers
   audit/backfill/forebet/ml_meta/parsers/research/sports but **never** `shadow_settle`.

**No fix was applied. Awaiting the owner's decision on the erratum question.**

---

## 3. LEAD #2 — substantially **clean**, with one real forward risk

- **Sport registry:** all 14 registered sports define `draw_possible`. Every `sport` string in
  every committed `considered_pool` resolves. All settled-date pool rows are `football`.
- **`rank_within_sport_day`:** no nulls among eligible entries (nulls appear only on
  ineligible entries, by design). Duplicate rank numbers exist across dates 2026-09-10+ but
  **never within the same sport** — verified per sport per date for all 10 dates: zero
  within-sport duplicates. Since rank is *per sport-day*, cross-sport repetition is expected
  and correct. The handoff's worry here is **unfounded**.
- **Real forward risk:** dates 2026-09-10 onward are multi-sport (basketball, hockey, rugby,
  volleyball, handball, cricket, mma, american_football, baseball). Ten of the 14 registered
  sports have `draw_possible = False`. Once those dates settle, corrupt rank-4+ rows with
  `winner_index == 0` will grade **UNRESOLVED** instead of FAILURE. So the FAILURE/UNRESOLVED
  split of the broken tier becomes sport-dependent — while SUCCESS stays unreachable in
  **every** sport (§1.2 tested all 14 plus unregistered strings).

---

## 4. LEAD #3 — **no silent drops**; accounting reconciles exactly

Distinct `considered_status` values across all committed evidence (exhaustive recursive scan
of every shadow manifest / settlement / selections file):

Counts are split by artifact type on purpose — a bare total would double-count, because the
10 manifests enumerate every considered record while the 3 settlement files only carry rows
that reached grading. (All 10 dates have manifests; only 2026-09-02/05/06 are settled.)

| status | in manifests | in settlement rows | graded? |
|---|---|---|---|
| `FEATURE_INCOMPLETE_OR_R2_INELIGIBLE` | 2633 | 0 | no (ineligible — correctly excluded) |
| `ELIGIBLE_RANKED_BEYOND_TOP3` | 1317 | 867 | **yes** |
| `TOP3_EVALUATION_COHORT` | 50 | 6 | yes (via `selections[]`) |
| `PRIMARY_SHADOW_SELECTION` | 28 | 3 | yes (via `selections[]`) |
| `EXACT_DECISION_DUPLICATE_OBSERVATION` | 3 | 0 | no (duplicate — correctly excluded) |

The other statuses the handoff named — `DECISION_CONFLICT_EXCLUDED`, `MALFORMED_OR_UNKEYABLE`,
`TIMING_REJECTED`, `IDENTITY_INELIGIBLE` — **are producible by `shadow_evaluator.py` but have
zero occurrences** in committed evidence, and every corresponding `decision_accounting`
counter is 0 across all 10 dates. **No row anywhere pairs an excluded status with a real
grade.** Nothing decided is being dropped.

Accounting reconciles exactly (all dates combined):
`primary 28 + cohort 50 + r4+ 1317 + incomplete 2633 = 4028 = admitted_canonical_records`,
and `decision_total_records 4031 = 4028 + 3 exact_decision_duplicate_extra_rows`. ✔

**Two caveats worth recording (neither is a bug):**

1. **Selection effect.** 2633/4031 = **65%** of records are excluded as
   feature-incomplete/R2-ineligible *before* grading. Every tier's rate is therefore
   *conditional on clearing R2*, not a population upset rate. Any prose reading these numbers
   as "how often underdogs win" overstates the claim.
2. **Two committed settlement artifacts exist for one run_id, with different schemas.**
   For run `acd78872019300ff` (2026-09-02):
   - `data/reports/shadow/2026-09-02/acd78872019300ff/settlement.json` — settle schema, key
     `grades`, 27 rows, fields `source` / `considered_status` / `rank_within_sport_day`.
   - `data/reports/shadow/settlements/2026-09-02/acd78872019300ff.settlement.json` — evaluator
     schema, key `graded_selections`, 3 rows, fields `selection_status` / `rank`.

   They **agree on all three overlapping rows** (verified event-by-event: same grade, same
   `underdog_index`, same `winner_index`) — so there is no contradiction. But they differ in
   scope, in key names, in status field names, and in filename pattern
   (`settlement.json` vs `*.settlement.json`). Any tool that discovers evidence by globbing
   **must** disambiguate these explicitly. This is the direct cause of §5.2.

---

## 5. LEAD #4 — Wilson math is **correct**; the reported `n` is **not reproducible**

### 5.1 The formula checks out (three independent derivations)

`statsmodels` is unavailable in this sandbox, so I did not rely on a library. I verified the
closed form against (i) a **first-principles score-test quadratic** — the Wilson interval is
*defined* as the set of `p₀` not rejected by `(p̂−p₀)² ≤ z²·p₀(1−p₀)/n`, giving
`A = 1+z²/n`, `B = −(2p̂+z²/n)`, `C = p̂²` — and (ii) a **brute-force scan** of the rejection
boundary at 2,000,000 steps.

| (wins, n) | closed form | score-test quadratic | agree |
|---|---|---|---|
| (1,3) | (0.061491944720, 0.792340399198) | (0.061491944720, 0.792340399198) | ✔ |
| (0,798) | (0.000000000000, 0.004790795959) | (0.000000000000, 0.004790795959) | ✔ |
| (270,820) | (0.297964079840, 0.362164702219) | (0.297964079840, 0.362164702219) | ✔ |
| (4,9) | (0.188778521098, 0.733348706505) | (0.188778521098, 0.733348706505) | ✔ |
| (0,1) / (1,1) / (5,5) / (7,7) | edge cases | identical | ✔ |
| (3,10) / (1,10) / (0,100000) / (50000,100000) | — | identical | ✔ |

All 12 cases agree to <1e-12; brute force agrees to grid resolution (~4e-7). The `n=0`
convention returning `(0.0, 1.0)` is a sane total-function choice. **The Wilson
implementation is not the problem.** (Caveat: this validates the standard formula, not
`certification.py`'s literal code, which is absent — §0.)

### 5.2 New finding — `n=798` matches **nothing** in the committed evidence, and I reconstructed why

Actual rank-4+ decided rows across the three settled dates:

| date | pool rows | FAILURE | SUCCESS | UNRESOLVED | UNSETTLED |
|---|---|---|---|---|---|
| 2026-09-02 | 24 | **22** | 0 | 2 | 0 |
| 2026-09-05 | 504 | **480** | 0 | 5 | 19 |
| 2026-09-06 | 339 | **318** | 0 | 8 | 13 |
| **total** | **867** | **820** | **0** | 15 | 32 |

No natural slice yields 798 — not pool rows (867), not FAILURE (820), not decided (820), not
unique event_ids (867), not all rows (876). But:

```
480 (09-05) + 318 (09-06) = 798      and      820 − 22 (09-02) = 798
```

**The reported figure silently omits all of 2026-09-02's rank-4+ rows.** Testing the most
probable mechanism — that the evidence glob resolved 2026-09-02 to the *evaluator-schema*
`*.settlement.json` (3 rows, **zero** pool entries; §4 caveat 2) rather than the settle-schema
`settlement.json` (27 rows, 24 pool entries) — reproduces the published numbers exactly:

```
ranks_4_plus decided : 798        (handoff: 798)          EXACT MATCH
primary              : 1 SUCCESS, 2 FAILURE
cohort_ranks_2_3     : 3 SUCCESS, 3 FAILURE
TOTAL decided        : 807        (handoff: "~810 across all 3 dates")  ✔
```

**Therefore the handoff's own coverage claim is false.** "798 of the ~810 total decided
outcomes across all 3 dates" — the rank-4+ tier actually spans **2 dates**, and the third
date's evidence was substituted by a different artifact for the same `run_id`. This is a
*second*, independent defect from §1: even if the `underdog_index` bug were fixed, the tier
counts would still be wrong by 22 rows and one date.

This is exactly the ambiguity §4 caveat 2 predicts, and it is why evidence discovery must key
on explicit schema+path contracts rather than filename globs.

### 5.3 LEAD #5 — **clean**, verified byte-for-byte

Recomputed sha256 from the actual bytes in this checkout and compared to each marker's first
field. All four match exactly:

| artifact | result |
|---|---|
| `2026-09-02/acd78872019300ff/settlement.json` (17,749 B) | **MATCH** `e30d90dc…0707c0` |
| `2026-09-05/4353ca88e825fd6a/settlement.json` (285,691 B) | **MATCH** `d8a901d8…34c84` |
| `2026-09-06/8d9a696cd42c1878/settlement.json` (193,764 B) | **MATCH** `2b0899f1…9d8d` |
| `settlements/2026-09-02/acd78872019300ff.settlement.json` (2,988 B) | **MATCH** `08f399e4…4afa` |

A matching sha256 over the full byte string is conclusive against truncation, re-encoding, and
line-ending mangling — any such alteration would change the digest. Markers are
LF-terminated `<hash>  <filename>` (confirmed with `cat -A`). **The `git show` recovery was
faithful. This lead is closed with no finding.**

---

## 6. LEAD #6 — meta, answered honestly

**Did the tool deliver on "no generic thumbsuck numbers"? Partly — and it missed the number
that mattered.**

- `z = 1.959963984540054` is a convention, not a derivation. It is a *defensible* default and
  should be declared and configurable, but it is not what failed here. Arguing about 95% vs
  99% while the numerator was hard-wired to zero is rearranging deck chairs.
- **The absence of a sample-size gate was not the gap either.** A gate on `n` would have
  *passed* — n=798 is large. The report's most confident line was confident *because* n was
  large.

**The actual failure mode:** a confidence interval quantifies **sampling error only**. It says
nothing whatsoever about **systematic error**. `0/798` produced a genuinely tight, genuinely
correct Wilson bound — around a quantity that was definitionally zero. The interval *laundered
a plumbing defect into the report's strongest finding*. Precision made it more dangerous, not
less: "Wilson UB ≈ 0.005" reads as rigour and actively discouraged anyone from checking
whether the numerator could ever be non-zero.

**So "no gate at all" is not the safest design — but the needed gate is a validity gate, not a
size gate.** Recommended precondition checks (cheap, no thumbsuck constants involved):

1. **Sentinel/validity assertion:** before computing any bound, assert every graded row has
   `underdog_index ∈ {1, 2}`. Fail loudly otherwise. This single check catches §1 instantly
   and requires no arbitrary threshold.
2. **Structural-zero detector:** if a tier reports 0 successes, do not emit a bound — emit
   `INVALID_INPUT_UNVERIFIABLE` until the numerator's reachability is demonstrated (e.g. by
   showing at least one row could have graded SUCCESS under the committed contract).
3. **Evidence-provenance assertion:** each tier must report the exact artifact paths, schema
   versions, and per-date row counts it consumed, and reconcile them against
   `decision_accounting`. §5.2's missing date would have been visible immediately as
   "2026-09-02: 0 rank-4+ rows read, 24 present in manifest".
4. **Coverage claim verification:** never assert "across all N dates" without enumerating the
   dates actually read.

Note the repo already had the right instinct — `tests/test_adversarial_review.py` exists as the
regression suite from a *previous* adversarial review. It simply never covered
`shadow_settle.py`. The fixture-divergence guard recommended in §2.6(c) belongs there.

---

## 7. Verdict summary

| lead | verdict |
|---|---|
| **#1** rank-4+ `underdog_index` defaults to sentinel 0 | **CONFIRMED, worse than described.** SUCCESS unreachable in 0/768 combos (handoff's "draw edge case" does not exist). True rate **270/820 = 32.9%**, not 0/798. Not a `certification.py` bug: `shadow_evaluator.py` omission + `shadow_settle.py` silent default + **test-fixture schema divergence**. Blast radius confined to `ranks_4_plus` (primary/cohort verified sound, 9/9 unchanged). |
| **#1.5** immutability | Erratum is **permissible and does not violate** `no_force_overwrite` or `anti_tuning`, provided the original artifacts stay byte-identical and the correction is published as a new artifact referencing their sha256. Corrected numbers must never justify a rule change. |
| **#2** sports / ranks | **Clean.** No within-sport rank duplicates on any date; all sport strings resolve. Forward risk: multi-sport dates will shift corrupt rows FAILURE→UNRESOLVED. |
| **#3** status coverage | **Clean.** 5 statuses in evidence; 4 named statuses producible but zero occurrences; accounting reconciles to the row (4028 + 3 = 4031). Caveats: 65% pre-grading exclusion (selection effect); two schemas per run_id. |
| **#4** Wilson math | **Formula correct** (verified 3 independent ways, 12 cases incl. edges). **But `n=798` is irreproducible** — it omits 2026-09-02 entirely; reconstructed exactly as 480+318 via the wrong-schema glob. "All 3 dates" claim is **false**. |
| **#5** file recovery | **Clean.** All 4 sha256 markers match byte-for-byte. No finding. |
| **#6** meta | z=95% is a convention but not the failure. No-size-gate is not the gap (n was large). Real gap: **no validity/provenance gate** — a Wilson bound quantifies sampling error and silently launders systematic error into apparent confidence. |

**Bottom line:** the prior agent's instinct was right and its lead was real, but it
under-called the severity (a structural zero, not an edge case), mis-stated the coverage
("3 dates" — actually 2), and mis-located the defect (it is upstream of `certification.py`,
in the evaluator's serialisation, the settler's silent default, and above all a test fixture
that asserts a schema production never emits). Its headline finding is not "confidently near
zero" — it is **confidently wrong by 270 rows**, and the corrected value (~33%) overturns the
conclusion rather than merely widening its error bar.

**Nothing was fixed. Two decisions are needed from the owner:** (1) authorise the append-only
erratum artifact for the three committed `settlement.json` files; (2) authorise the forward
code + fixture fixes (identity serialised into pool dicts; loud failure instead of
`.get(..., 0)`; fixture rebuilt from production schema with a divergence guard, housed in
`tests/test_adversarial_review.py`).
