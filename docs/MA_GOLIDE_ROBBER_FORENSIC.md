# Ma Golide Robber Forensic Specification

## Authoritative meaning selected for Slumdog

The Gold Universe contains two uses of the word `ROBBER`:

1. The Assayer labels an unreliable historical slice `ROBBER` when `n < 10` or
   win rate is below 50%; this is a warning/fade classification.
2. The Satellite/Mothership Robber engine identifies and emits an upset
   participant. This is the betting-product meaning.

Slumdog reproduces meaning 2. Meaning 1 is retained only as a future purity
warning and must never create a candidate by itself.

## Source of truth

`Ma_Golide_Satellites/docs/Accumulator_Builder.gs`:

- `ROBBERS_CONFIG_DEFAULTS`
- `detectRobbers`
- `_robbers_normalizePick_`
- `detectAllRobbers`

The current Python `game_enricher.py` is explicitly a Phase-1 bridge and admits
that a full Forecaster port is future work. It classifies an already-selected
pick as Robber when its odds exceed its opponent's odds. It is not the complete
detector.

## Underdog identity cascade

1. If both prices exist, higher decimal odds identifies the underdog.
2. Otherwise select the participant opposite Forebet's predicted winner.
3. Otherwise select the lower Forebet participant probability.
4. Otherwise select the weaker recent-form side; ties default to participant 1.

Draw is not a named participant and is never the Slumdog candidate.

## Legacy score

| Factor | Rule | Points |
|---|---|---:|
| Favourite strength | price <=1.35 / <=1.55 / <=1.75 / other | 15 / 12 / 8 / 3 |
| H2H upset history | dog win rate >=30% / any prior dog win | 20 / 6 |
| Period dominance | >50% in >=2 periods / one period | 12 / 5 |
| Half performance | >50% first segment; >50% second segment | 5 + 5 |
| Recent momentum | >=55% over at least 5 / >=45% | 15 / 8 |
| Dog price | 2.50–5.50 / 2.00–2.49 / 5.51–8.00 / other | 15 / 10 / 8 / 4 |

Default score threshold is 20. Without prices it becomes
`max(10, round(20 × 0.55)) = 11`.

Legacy raw confidence is:

```text
min(80, 46 + 0.55 × score)
```

## Legacy price calibration warning

When price exists, Ma Golide shrinks raw probability toward implied probability,
then imposes:

- maximum probability 67%;
- minimum probability at least 30%;
- minimum probability advantage 8 percentage points over implied probability.

This means positive advantage can be manufactured by the bounding rule. Slumdog
reproduces the numbers for forensic agreement but marks them
`legacy_calibration_forensic=true`. They cannot certify a candidate.

The sport-specific ML-meta model must learn the true underdog-win probability
from pre-event data and out-of-time outcomes.

## Legacy implementation defects retained as findings, not behavior

- Configured odds bounds only emitted warnings and did not reject candidates.
- Missing odds lowered the score threshold, increasing output volume.
- Confidence was a deterministic score transform, not empirical calibration.
- Robbers were sorted by confidence but not capped; the UI showed only a top-3
  preview while the returned candidate array contained every Robber.

Slumdog likewise has no output-count cap, but every candidate row remains shadow.
