# Timing-V2 Proposal: Kickoff-Aware Pre-Event Gate (DRAFT)

> **Status: PROPOSAL ONLY — NOT IMPLEMENTED, NOT APPLIED TO ANY DATE**
>
> This document is a prospective design brief. No code changes, no
> configuration edits, no retroactive application to any past or
> already-frozen date. The frozen timing-v1 contract
> (`safe_cutoff_offset_hours_utc = 24`, anchored at
> `target_date 00:00 UTC − 24h`) remains canonical for all existing
> runs. The 2026-09-04 `NOT_EVALUATED` status is final.

## 1. Problem with Timing-V1

The v1 gate is ``captured_at ≤ target_date 00:00 UTC − 24h``. This
creates a structural gap: at any moment during UTC day D, the cutoff
for target date D+1 has already passed (it's D−1 00:00 UTC), so
**D+1 is structurally unreachable under v1**. The earliest reachable
target is always **D+2**, with a margin that depends on the time of
day the run executes.

This is not a bug — it's a conservative safety margin — but it means
one day's worth of predictions is permanently lost to the gap. For a
daily batch running at 04:00 UTC, D+2's cutoff (D+1 00:00 UTC) is
20 hours away, giving ample margin. But D+1 events whose kickoffs are
in the afternoon/evening of D+1 would be legitimately pre-event at
04:00 UTC on D.

## 2. Proposed Timing-V2 Contract

**Gate:** ``decision_committed_at < min(kickoff_utc) for all events in the selection``

Instead of anchoring the cutoff to ``target_date 00:00 UTC − 24h``,
v2 anchors it to each event's actual kickoff time. The decision must
be committed before the **earliest** kickoff among the selected events
for a given sport-day.

### Required validation

1. **Kickoff timestamp availability:** Every Forebet listing row must
   expose a parseable kickoff timestamp (date + time + timezone). This
   needs to be verified across all 12+ sports and all page formats.

2. **Kickoff provenance:** The kickoff timestamp must be captured from
   the pre-event listing page and retained as a provenance field on the
   ``PreEventRecord``. It must be verified to be present and parseable
   before the event is admitted to the evaluation pipeline.

3. **Timezone handling:** Kickoff times on Forebet are displayed in
   various timezone conventions. The v2 contract requires explicit
   UTC conversion with a recorded source timezone.

4. **Safety margin:** A configurable margin (default: 1 hour) is
   subtracted from the earliest kickoff to produce the effective
   cutoff. This accounts for:
   - Clock skew between the decision machine and the event venue
   - Capture receipt writing latency
   - Edge cases where kickoff times change (rescheduled matches)

### Formal specification

```
kickoff_utc(e)       = parse_kickoff(e.kickoff_raw, e.kickoff_tz)
effective_cutoff(e)  = kickoff_utc(e) - margin_hours
decision_valid(e)    = decision_committed_at < effective_cutoff(e)
sport_day_valid(sd)  = all(decision_valid(e) for e in sd.selections)
```

### Minimum margin

The safety margin MUST be at least 1 hour. The exact value is a
configuration parameter in the declaration but must be verified at
load time (same fail-closed pattern as the v1 24h offset).

## 3. Migration plan

### Prospective only

- V2 applies ONLY to target dates that have NOT been frozen under v1.
- Every date already evaluated under v1 retains its v1 timing
  assessment permanently.
- The 2026-09-04 ``NOT_EVALUATED`` status remains final — v2 does not
  retroactively include it.

### Dual-gate transition

During the transition period (first 30 days after v2 activation):

1. Both v1 AND v2 gates are evaluated for every new date.
2. An event passes if it satisfies EITHER gate.
3. Both gate results are recorded in the manifest.
4. After the transition, v2 becomes the sole gate (v1 retained as
   a historical annotation).

### Configuration

A new declaration field:

```json
{
  "timing_version": "v2",
  "timing_v2": {
    "kickoff_field": "kickoff",
    "kickoff_tz_field": "kickoff_tz",
    "margin_hours": 1,
    "require_kickoff_present": true,
    "fallback_to_v1_if_kickoff_missing": false
  }
}
```

``fallback_to_v1_if_kickoff_missing``: if ``true``, events without a
parseable kickoff fall back to the v1 gate. If ``false`` (default),
events without kickoff are excluded from evaluation.

## 4. Validation required before implementation

Before any code is written:

1. **Kickoff coverage audit:** Parse every existing capture receipt
   across all sports and measure the percentage of events with a
   parseable kickoff timestamp. Report by sport.

2. **Kickoff format inventory:** Catalog the distinct kickoff formats
   observed (ISO 8601, ``DD/MM/YYYY HH:MM``, ``HH:MM`` with implied
   date, etc.) and the timezone conventions.

3. **Kickoff stability check:** For events captured multiple times
   (same event, different dates), verify that kickoff times don't
   change between captures (or record the change as a provenance
   event).

4. **Edge case inventory:** Identify sports/events where kickoff
   times are not available or are unreliable (e.g., multi-day cricket,
   MMA cards where individual fight times aren't listed, esoccer with
   rolling start times).

## 5. What v2 does NOT change

- The frozen R2 eligibility rule (unchanged).
- The R1 ranking comparator (unchanged).
- The per-sport-day cohort policy (unchanged).
- The grading contract for settlement (unchanged).
- The no-overwrite, atomic-write artifact protocol (unchanged).
- The Forebet politeness policy (unchanged).
- Any frozen configuration (unchanged).

## 6. Honest limitations

- Kickoff times can change after the capture (match rescheduling,
  weather postponement). V2 inherits this risk; the v1 24h margin
  was more robust against this specific failure mode.
- Not all sports expose reliable kickoff times on the listing page.
  Cricket multi-day matches, MMA cards, and esoccer are known
  problem areas.
- The v2 margin trades off coverage for safety: a 1-hour margin
  captures more events than v1's 24h margin but is less conservative.
- V2 requires MORE data (kickoff timestamps) than v1, increasing
  the parser surface and the potential for parsing errors.

## 7. Decision record

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-03 | V2 proposal drafted | Owner-requested prospective design |
| — | NOT IMPLEMENTED | Awaiting kickoff coverage audit + owner approval |
