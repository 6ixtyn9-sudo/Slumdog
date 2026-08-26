"""Milestone 6A — incremental v2 research builder tests.

Three families:
1. Strict equivalence: for valid canonical settled events where the legacy
   and v2 history memberships agree, the v2 incremental builder produces
   bit-identical examples and matching counters to the strict reference
   builder (build_price_free_examples).
2. Intentional divergences: corrected v2 history membership
   (research_history_eligible) deliberately differs from legacy
   HistoryIndex quirks (void aliases, incoherent disposition/winner rows)
   — these are NOT equivalence cases.
3. Streaming/artifact behavior: incremental emitter, bounded sample,
   gzip line counts, exact-byte digests, failure semantics (mid-stream and
   mid-commit failures leave no final artifacts), no-preexist refusal,
   diagnostic receipts, one-shot iterator consumption.

Memory-boundedness is verified by real run evidence (Codespace), not here.
"""

import gzip
import hashlib
import json
import os
import random
import tempfile
from pathlib import Path

from slumdog.dataset import (
    ValidEventWithSource,
    PriceFreeUnderdogExample,
    _canonical_event_repr,
    _validate_settled_dict,
    build_price_free_examples,
)
from slumdog.dataset_audit import audit_dataset
from slumdog.research_builder import (
    RESEARCH_FEATURE_CONTRACT_VERSION,
    _compute_research_input_digest,
    _exclusion_counter_name,
    _normalize_duplicates,
    _pick_representative,
    _source_location_key,
    research_history_eligible,
)
from slumdog.research_dataset import (
    NOT_READY_STATUS,
    RESEARCH_STATUS,
    ResearchExampleEmitter,
    build_research_dataset,
    run_research_mode,
)

FEATURE_CONTRACT_V2 = RESEARCH_FEATURE_CONTRACT_VERSION
LABEL_CONTRACT_V1 = "price-free-v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_row(
    event_id="hockey:1",
    sport="hockey",
    event_date="2023-08-20",
    p1="Alpha",
    p2="Beta",
    prob1=0.6,
    prob2=0.4,
    draw_prob=None,
    winner=1,
    score1=3,
    score2=1,
    disposition="SETTLED",
    source_url="",
    raw_sha256=None,
    league="L",
):
    facets = {"raw_sha256": raw_sha256} if raw_sha256 is not None else {}
    return {
        "event_id": event_id,
        "sport": sport,
        "event_date": event_date,
        "participant_1": p1,
        "participant_2": p2,
        "winner_index": winner,
        "score_1": score1,
        "score_2": score2,
        "probability_1": prob1,
        "probability_2": prob2,
        "draw_probability": draw_prob,
        "forebet_pick": 1,
        "odds_1": None,
        "odds_2": None,
        "league": league,
        "period_scores_1": (1, 1, 1),
        "period_scores_2": (0, 0, 0),
        "source_url": source_url,
        "disposition": disposition,
        "facets": facets,
    }


def vws(rows: list[dict], source_file="f.jsonl.gz") -> list[ValidEventWithSource]:
    return [
        ValidEventWithSource(
            event=_validate_settled_dict(r),
            source_file=source_file,
            source_location=f"line:{i + 1}",
        )
        for i, r in enumerate(rows)
    ]


def strict_build(rows):
    events = [_validate_settled_dict(r) for r in rows]
    examples, receipt = build_price_free_examples(events)
    return examples, receipt


def research_build(rows, sample_size=100):
    emitter = ResearchExampleEmitter(None, sample_size=sample_size)
    result = build_research_dataset(
        vws(rows),
        raw_input_rows=len(rows),
        schema_excluded_rows=0,
        malformed_empty_participant_rows=0,
        emitter=emitter,
    )
    return result, emitter


def stripped(example) -> dict:
    d = example.to_dict()
    d.pop("feature_contract_version")  # v1 vs v2 is the only sanctioned diff
    return d


def stripped_list(examples) -> list[dict]:
    return [stripped(e) for e in examples]


# All rows here are builder-eligible or excluded identically by both
# builders, and v1/v2 history memberships agree (valid canonical settled
# events) — equivalence scope.
EQUIV_ROWS = [
    # d1
    make_row("hockey:1", event_date="2023-01-02", p1="Alpha", p2="Beta", prob1=0.6, prob2=0.4, winner=1, score1=3, score2=1),
    make_row("hockey:2", event_date="2023-01-02", p1="Gamma", p2="Delta", prob1=0.55, prob2=0.45, winner=2, score1=0, score2=2),
    # d2 — three rows; same-date isolation probe (Delta vs Alpha shares the
    # date with Beta vs Delta and Beta vs Gamma)
    make_row("hockey:3", event_date="2023-01-03", p1="Beta", p2="Gamma", prob1=0.45, prob2=0.55, winner=2, score1=1, score2=4),
    make_row("hockey:4", event_date="2023-01-03", p1="Delta", p2="Alpha", prob1=0.5, prob2=0.5, winner=1, score1=2, score2=1),
    make_row("hockey:5", event_date="2023-01-03", p1="Beta", p2="Delta", prob1=0.6, prob2=0.4, winner=1, score1=3, score2=0),
    # d3 — fractional scores (float accumulation order probe) + missing scores
    make_row("hockey:6", event_date="2023-01-04", p1="Alpha", p2="Beta", prob1=0.7, prob2=0.3, winner=2, score1=1.5, score2=2.5),
    make_row("hockey:7", event_date="2023-01-04", p1="Gamma", p2="Delta", prob1=0.5, prob2=0.5, winner=1, score1=None, score2=None),
    make_row("hockey:8", event_date="2023-01-04", p1="Delta", p2="Gamma", prob1=0.4, prob2=0.6, winner=1, score1=0.5, score2=1.0),
    # d4
    make_row("hockey:9", event_date="2023-01-05", p1="Alpha", p2="Gamma", prob1=0.65, prob2=0.35, winner=1, score1=4, score2=2),
    make_row("hockey:10", event_date="2023-01-05", p1="Beta", p2="Delta", prob1=0.5, prob2=0.5, winner=2, score1=1, score2=3),
]

MULTISPORT_ROWS = [
    *EQUIV_ROWS,
    make_row("basketball:1", sport="basketball", event_date="2023-01-02", p1="Raptors", p2="Celtics", prob1=0.4, prob2=0.6, winner=2, score1=95, score2=110),
    make_row("basketball:2", sport="basketball", event_date="2023-01-03", p1="Celtics", p2="Raptors", prob1=0.55, prob2=0.45, winner=1, score1=105, score2=99),
]


def write_ledger(root: Path, rows: list[dict]) -> None:
    interim = root / "interim"
    interim.mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    (interim / "settled_history.json").write_text(json.dumps(rows))


def run_audit_research(root: Path, out: Path, rows, *, examples=True, sample_size=5):
    receipt = out / "receipt.json"
    sample = out / "sample.json"
    examples_path = out / "examples.jsonl.gz" if examples else None
    code = audit_dataset(
        root, receipt, sample, sample_size,
        research_exclude_conflicts=True, examples_path=examples_path,
    )
    return code, receipt, sample, examples_path


# ---------------------------------------------------------------------------
# 1. Strict equivalence (valid canonical settled events)
# ---------------------------------------------------------------------------


def test_equivalence_examples_bit_identical_single_sport():
    strict_ex, _strict_receipt = strict_build(EQUIV_ROWS)
    result, _emitter = research_build(EQUIV_ROWS)
    assert result.ready is True
    assert stripped_list(strict_ex) == stripped_list(result.sample)


def test_equivalence_counters_and_outcomes():
    _strict_ex, strict_receipt = strict_build(EQUIV_ROWS)
    result, _emitter = research_build(EQUIV_ROWS)
    acc = result.receipt["accounting"]
    out = result.receipt["outcomes"]
    g = result.receipt["readiness"]["global"]
    assert strict_receipt.eligible_examples == acc["eligible_examples"]
    assert strict_receipt.builder_excluded_rows == acc["builder_excluded_rows"]
    assert strict_receipt.canonical_input_rows == acc["canonical_non_conflicting_rows"]
    assert strict_receipt.exact_duplicates_collapsed == acc["exact_duplicates_collapsed"]
    assert strict_receipt.positive_underdog_wins == out["positive_underdog_wins"]
    assert strict_receipt.negative_favorite_wins == out["negative_favorite_wins"]
    assert strict_receipt.negative_draws == out["negative_draws"]
    assert strict_receipt.positive_rate == out["positive_rate"]
    prov = g["provenance"]
    assert strict_receipt.provenance_present == prov["present"]
    assert strict_receipt.provenance_missing == prov["missing"]
    assert strict_receipt.provenance_invalid == prov["invalid"]
    assert strict_receipt.eligible_date_min == g["date_min"]
    assert strict_receipt.eligible_date_max == g["date_max"]
    for sport, counter in strict_receipt.per_sport.items():
        rs = result.receipt["readiness"]["by_sport"][sport]
        assert counter["eligible_examples"] == rs["eligible_examples"]
        assert counter.get("positive_underdog_wins", 0) == rs["positive_examples"]
        assert counter.get("negative_favorite_wins", 0) + counter.get("negative_draws", 0) == rs["negative_favorite_wins"]


def test_equivalence_same_date_isolation_explicit():
    rows = [
        make_row("hockey:1", event_date="2023-02-01", p1="A", p2="B", prob1=0.7, prob2=0.3, winner=1, score1=5, score2=2),
        make_row("hockey:2", event_date="2023-02-02", p1="B", p2="C", prob1=0.6, prob2=0.4, winner=1, score1=4, score2=1),
        make_row("hockey:3", event_date="2023-02-02", p1="C", p2="B", prob1=0.4, prob2=0.6, winner=2, score1=2, score2=3),
        make_row("hockey:4", event_date="2023-02-03", p1="B", p2="C", prob1=0.7, prob2=0.3, winner=1, score1=6, score2=0),
    ]
    strict_ex, _ = strict_build(rows)
    result, _emitter = research_build(rows)
    by_id_s = {e.event_id: e for e in strict_ex}
    by_id_r = {e.event_id: e for e in result.sample}
    assert set(by_id_s) == set(by_id_r)
    # hockey:3 (2023-02-02, C underdog / B favorite): C's prior must exclude
    # same-date hockey:2 -> 0 games; B's prior includes only 2023-02-01 -> 1.
    assert by_id_s["hockey:3"].features["underdog_prior_games"] == 0.0
    assert by_id_s["hockey:3"].features["favorite_prior_games"] == 1.0
    assert by_id_r["hockey:3"].features == by_id_s["hockey:3"].features
    # hockey:4 (2023-02-03, C underdog): both 2023-02-02 rows are prior now.
    assert by_id_r["hockey:4"].features == by_id_s["hockey:4"].features
    assert by_id_s["hockey:4"].features["underdog_prior_games"] == 2.0
    assert by_id_s["hockey:4"].features["favorite_prior_games"] == 3.0


def test_equivalence_input_reordering():
    rows = list(EQUIV_ROWS)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert shuffled != rows

    s1, _ = strict_build(rows)
    s2, _ = strict_build(shuffled)
    r1, _ = research_build(rows)
    r2, _ = research_build(shuffled)
    assert stripped_list(s1) == stripped_list(s2)
    assert stripped_list(r1.sample) == stripped_list(r2.sample)
    assert stripped_list(s1) == stripped_list(r1.sample)
    assert r1.examples_digest == r2.examples_digest
    assert r1.input_digest == r2.input_digest


def test_equivalence_multi_sport_content_and_artifact_ordering():
    strict_ex, _ = strict_build(MULTISPORT_ROWS)
    result, _emitter = research_build(MULTISPORT_ROWS, sample_size=200)
    strict_set = sorted(json.dumps(d, sort_keys=True) for d in stripped_list(strict_ex))
    research_set = sorted(json.dumps(d, sort_keys=True) for d in stripped_list(result.sample))
    assert strict_set == research_set
    # Research artifact order: sport -> event_date -> event_id.
    research_order = [(e.sport, e.event_date, e.event_id) for e in result.sample]
    assert research_order == sorted(research_order)
    # Strict reference order: event_date -> sport -> event_id.
    strict_order = [(e.event_date, e.sport, e.event_id) for e in strict_ex]
    assert strict_order == sorted(strict_order)


def test_equivalence_duplicates_identical_provenance_collapse():
    rows = [
        make_row("hockey:1", event_date="2023-03-01", source_url="https://x/1"),
        make_row("hockey:1", event_date="2023-03-01", source_url="https://x/1"),  # exact dup
        make_row("hockey:2", event_date="2023-03-02", p1="Beta", p2="Alpha", source_url="https://x/2"),
    ]
    strict_ex, strict_receipt = strict_build(rows)
    result, _emitter = research_build(rows, sample_size=10)
    assert strict_receipt.exact_duplicates_collapsed == 1
    assert result.receipt["accounting"]["exact_duplicates_collapsed"] == 1
    assert stripped_list(strict_ex) == stripped_list(result.sample)


def test_equivalence_duplicate_missing_vs_present_provenance():
    provenanced = make_row("hockey:1", event_date="2023-03-01", source_url="https://x/1", raw_sha256="ab" * 32)
    bare = make_row("hockey:1", event_date="2023-03-01")
    for rows in ([bare, provenanced], [provenanced, bare]):
        strict_ex, strict_receipt = strict_build(rows)
        result, _emitter = research_build(rows, sample_size=10)
        assert stripped_list(strict_ex) == stripped_list(result.sample)
        # The provenanced variant is the representative under both policies.
        assert result.sample[0].raw_sha256 == "ab" * 32
        assert result.sample[0].source_url == "https://x/1"
        assert strict_receipt.exact_duplicates_collapsed == 1
        assert result.receipt["accounting"]["exact_duplicates_collapsed"] == 1


# ---------------------------------------------------------------------------
# 2. Intentional divergences (corrected v2 history membership)
# ---------------------------------------------------------------------------


def test_divergence_void_alias_feeds_legacy_history_not_v2():
    rows = [
        make_row("hockey:1", event_date="2023-04-01", p1="Alpha", p2="Beta", disposition="NO_CONTEST", winner=1, score1=2, score2=1, prob1=0.5, prob2=0.5),
        make_row("hockey:2", event_date="2023-04-02", p1="Beta", p2="Gamma", prob1=0.4, prob2=0.6, winner=1, score1=3, score2=2),
    ]
    strict_ex, _ = strict_build(rows)
    result, _emitter = research_build(rows)
    assert result.ready is True
    strict_beta = next(e for e in strict_ex if e.event_id == "hockey:2")
    research_beta = next(e for e in result.sample if e.event_id == "hockey:2")
    # Legacy HistoryIndex includes NO_CONTEST rows (only literal VOID is
    # filtered); v2 eligibility excludes them. Beta is the underdog here.
    assert strict_beta.features["underdog_prior_games"] == 1.0
    assert research_beta.features["underdog_prior_games"] == 0.0
    # Everything else on the row is identical (features/missingness carry
    # the intentional divergence: prior win rate 0.0 vs None).
    a = stripped(strict_beta)
    b = stripped(research_beta)
    for key in a:
        if key not in ("features", "missingness"):
            assert a[key] == b[key]
    assert a["missingness"]["underdog_prior_win_rate"] == 0
    assert b["missingness"]["underdog_prior_win_rate"] == 1


def test_divergence_settled_cup_winner_zero_feeds_legacy_history_not_v2():
    rows = [
        make_row("football:1", sport="football", event_date="2023-04-01", p1="Alpha", p2="Beta", disposition="SETTLED_CUP", winner=0, score1=1, score2=1, prob1=0.5, prob2=0.5),
        make_row("football:2", sport="football", event_date="2023-04-02", p1="Beta", p2="Gamma", prob1=0.4, prob2=0.6, winner=1, score1=3, score2=2),
    ]
    strict_ex, _ = strict_build(rows)
    result, _emitter = research_build(rows)
    assert result.ready is True
    strict_beta = next(e for e in strict_ex if e.event_id == "football:2")
    research_beta = next(e for e in result.sample if e.event_id == "football:2")
    # Legacy includes SETTLED_CUP winner-0 rows in draw-capable sports;
    # v2 requires coherent disposition/winner combinations.
    assert strict_beta.features["underdog_prior_games"] == 1.0
    assert research_beta.features["underdog_prior_games"] == 0.0


def test_research_history_eligible_matrix():
    def ev(**kw):
        base = dict(
            event_id="x:1", sport="hockey", event_date="2023-01-01",
            participant_1="A", participant_2="B", winner_index=1,
            score_1=1, score_2=0, probability_1=0.6, probability_2=0.4,
            draw_probability=None, forebet_pick=1, odds_1=None, odds_2=None,
            league="", period_scores_1=(), period_scores_2=(),
            source_url="", disposition="SETTLED",
        )
        base.update(kw)
        return _validate_settled_dict(base)

    assert research_history_eligible(ev()) is True
    assert research_history_eligible(ev(winner_index=2)) is True
    # two-way draw: incoherent under both contracts
    assert research_history_eligible(ev(winner_index=0)) is False
    # draw-capable sport: SETTLED winner 0 is coherent
    assert research_history_eligible(ev(sport="football", winner_index=0)) is True
    # cup
    assert research_history_eligible(ev(disposition="SETTLED_CUP", winner_index=1)) is True
    assert research_history_eligible(ev(disposition="SETTLED_CUP", winner_index=0)) is False
    # explicit draw
    assert research_history_eligible(ev(sport="football", disposition="SETTLED_DRAW", winner_index=0)) is True
    assert research_history_eligible(ev(disposition="SETTLED_DRAW", winner_index=0)) is False
    assert research_history_eligible(ev(disposition="SETTLED_DRAW", winner_index=1, sport="football")) is False
    # void / aliases
    assert research_history_eligible(ev(disposition="VOID", winner_index=1)) is False
    assert research_history_eligible(ev(disposition="NO_CONTEST", winner_index=1)) is False
    # unknown sport / degenerate keys
    assert research_history_eligible(ev(sport="quidditch")) is False
    assert research_history_eligible(ev(participant_2="A")) is False


# ---------------------------------------------------------------------------
# 3. Streaming and artifact behavior
# ---------------------------------------------------------------------------


def test_normalize_content_mismatch_fails_closed():
    rows = [
        make_row("hockey:1", event_date="2023-05-01", winner=1),
        make_row("hockey:1", event_date="2023-05-01", winner=2),  # content conflict
    ]
    canonical, collapsed, errors = _normalize_duplicates(vws(rows))
    assert any("internal_content_mismatch" in e for e in errors)
    assert canonical == []
    assert collapsed == 0


def test_v2_input_digest_exact_bytes():
    events = [_validate_settled_dict(r) for r in EQUIV_ROWS]
    rows_by_sport = {"hockey": sorted(events, key=lambda r: (r.event_date, r.event_id))}
    combined, sport_digests = _compute_research_input_digest(rows_by_sport)
    h = hashlib.sha256()
    for r in rows_by_sport["hockey"]:
        canon = _canonical_event_repr(r)
        h.update((json.dumps(canon, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    expected_sport = h.hexdigest()
    expected_combined = hashlib.sha256(
        b"slumdog-research-input-v2\n"
        + f"hockey\n{len(rows_by_sport['hockey'])}\n{expected_sport}\n".encode("utf-8")
    ).hexdigest()
    assert sport_digests["hockey"] == expected_sport
    assert combined == expected_combined
    assert len(combined) == 64
    assert len(expected_sport) == 64


def minimal_example(i: int, label: int = 1) -> PriceFreeUnderdogExample:
    return PriceFreeUnderdogExample(
        event_id=f"hockey:e{i}",
        sport="hockey",
        event_date=f"2023-06-0{i}",
        favorite_index=1,
        underdog_index=2,
        favorite_probability=0.7,
        underdog_probability=0.3,
        draw_probability=None,
        probability_gap=0.4,
        label=label,
        features={"underdog_prior_games": float(i)},
        missingness={"underdog_prior_games": 0},
    )


def test_emitter_incremental_digest_sample_and_count():
    emitter = ResearchExampleEmitter(None, sample_size=2)
    examples = [minimal_example(i) for i in range(1, 6)]
    payload = b""
    for e in examples:
        payload += (json.dumps(e.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        emitter.emit(e)
    assert emitter.emitted == 5
    assert [e.event_id for e in emitter.sample] == ["hockey:e1", "hockey:e2"]
    assert len(emitter.sample) == 2  # bounded, never exceeds sample_size
    assert emitter.digest == hashlib.sha256(payload).hexdigest()
    assert emitter.tmp_path is None  # file-less mode


def test_gzip_line_count_and_final_bytes(tmp_path):
    rows = [
        make_row(f"hockey:{i}", event_date=f"2023-06-0{i}", p1="Alpha", p2="Beta", winner=1 if i % 2 else 2, score1=2, score2=1, prob1=0.6, prob2=0.4)
        for i in range(1, 7)
    ]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    code, receipt, sample, examples_path = run_audit_research(root, out, rows, sample_size=2)
    assert code == 0
    gz = gzip.decompress(examples_path.read_bytes())
    lines = gz.decode("utf-8").splitlines()
    assert len(lines) == 6
    receipt_data = json.loads(receipt.read_text())
    assert receipt_data["examples_digest"] == hashlib.sha256(gz).hexdigest()
    assert receipt_data["accounting"]["eligible_examples"] == 6
    sample_data = json.loads(sample.read_text())
    assert len(sample_data["examples"]) == 2
    # sample lines are the first two emitted lines, byte-identical
    assert json.dumps(sample_data["examples"][0], sort_keys=True, separators=(",", ":")) == lines[0]


def test_mid_stream_failure_leaves_no_final_artifacts(tmp_path, monkeypatch):
    import slumdog.research_dataset as rd

    real_gzipfile = rd.gzip.GzipFile
    state = {"writes": 0}

    class BoomGzip:
        def __init__(self, *args, **kwargs):
            self._inner = real_gzipfile(*args, **kwargs)

        def write(self, data):
            state["writes"] += 1
            if state["writes"] >= 3:
                raise RuntimeError("injected mid-stream failure")
            self._inner.write(data)

        def close(self):
            self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(rd.gzip, "GzipFile", BoomGzip)

    rows = [
        make_row(f"hockey:{i}", event_date=f"2023-06-0{i}", p1="Alpha", p2="Beta", winner=1, score1=2, score2=1, prob1=0.6, prob2=0.4)
        for i in range(1, 7)
    ]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    code, receipt, sample, examples_path = run_audit_research(root, out, rows, sample_size=2)
    assert code == 1
    assert state["writes"] >= 3  # failure happened mid-stream, after rows emitted
    assert not examples_path.exists()
    assert not sample.exists()
    assert not receipt.exists()
    assert [p for p in out.iterdir() if ".tmp-" in p.name] == []


def test_mid_commit_failure_removes_this_run_finals(tmp_path, monkeypatch):
    import slumdog.research_dataset as rd

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # second rename = sample; examples already renamed
            raise OSError("injected rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr(rd.os, "replace", flaky_replace)

    rows = [
        make_row(f"hockey:{i}", event_date=f"2023-06-0{i}", p1="Alpha", p2="Beta", winner=1, score1=2, score2=1, prob1=0.6, prob2=0.4)
        for i in range(1, 4)
    ]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    code, receipt, sample, examples_path = run_audit_research(root, out, rows, sample_size=2)
    assert code == 1
    assert calls["n"] == 2
    # This run's finals and temps are all gone; no ready receipt.
    assert not examples_path.exists()
    assert not sample.exists()
    assert not receipt.exists()
    assert [p for p in out.iterdir() if ".tmp-" in p.name] == []


def test_no_preexisting_output_refusal(tmp_path):
    rows = [make_row("hockey:1", event_date="2023-06-01")]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    receipt = out / "receipt.json"
    receipt.write_text('{"sentinel": true}')
    code, _, _, examples_path = run_audit_research(root, out, rows, sample_size=2)
    assert code == 1
    assert json.loads(receipt.read_text()) == {"sentinel": True}
    assert not examples_path.exists()
    assert not (out / "sample.json").exists()

    # Pre-existing examples path is refused too.
    out2 = tmp_path / "out2"
    out2.mkdir()
    (out2 / "examples.jsonl.gz").write_bytes(b"pre-existing")
    code2, receipt2, sample2, examples2 = run_audit_research(root, out2, rows, sample_size=2)
    assert code2 == 1
    assert examples2.read_bytes() == b"pre-existing"
    assert not receipt2.exists()
    assert not sample2.exists()


def test_diagnostic_receipt_on_internal_inconsistency(tmp_path):
    rows = [make_row("hockey:1", event_date="2023-06-01")]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    # Deliberately inconsistent accounting inputs -> unbalanced -> not ready.
    code = run_research_mode(
        valid_with_source=vws(rows),
        raw_input_rows=99,
        schema_excluded_rows=0,
        malformed_empty_participant_rows=0,
        schema_exclusion_reasons={},
        receipt_path=out / "receipt.json",
        sample_path=out / "sample.json",
        examples_path=out / "examples.jsonl.gz",
        sample_size=5,
        files_found=1,
        files_empty=0,
        files_unreadable=0,
        file_errors=[],
    )
    assert code == 1
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == NOT_READY_STATUS
    assert receipt["research_ready"] is False
    assert any("accounting_invariants_unbalanced" in e for e in receipt["errors"])
    # Diagnostic receipt never coexists with final examples/sample artifacts.
    assert not (out / "sample.json").exists()
    assert not (out / "examples.jsonl.gz").exists()
    assert [p for p in out.iterdir() if ".tmp-" in p.name] == []


def test_ready_receipt_flags_and_v2_versions(tmp_path):
    rows = [
        make_row("hockey:1", event_date="2023-06-01"),
        make_row("hockey:2", event_date="2023-06-02", p1="Beta", p2="Alpha", winner=2, score1=0, score2=3, prob1=0.4, prob2=0.6),
    ]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    code, receipt, sample, examples_path = run_audit_research(root, out, rows, sample_size=5)
    assert code == 0
    receipt_data = json.loads(receipt.read_text())
    assert receipt_data["status"] == RESEARCH_STATUS
    assert receipt_data["research_ready"] is True
    assert receipt_data["feature_contract_version"] == FEATURE_CONTRACT_V2
    assert receipt_data["label_contract_version"] == LABEL_CONTRACT_V1
    sample_data = json.loads(sample.read_text())
    assert sample_data["feature_contract_version"] == FEATURE_CONTRACT_V2
    assert sample_data["research_only"] is True
    for line in gzip.decompress(examples_path.read_bytes()).decode().splitlines():
        assert json.loads(line)["feature_contract_version"] == FEATURE_CONTRACT_V2


def test_receipt_only_run_without_examples(tmp_path):
    rows = [make_row(f"hockey:{i}", event_date=f"2023-06-0{i}") for i in range(1, 4)]
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, rows)
    code, receipt, sample, examples_path = run_audit_research(root, out, rows, examples=False, sample_size=5)
    assert code == 0
    assert examples_path is None
    assert not (out / "examples.jsonl.gz").exists()
    receipt_data = json.loads(receipt.read_text())
    sample_data = json.loads(sample.read_text())
    assert receipt_data["accounting"]["eligible_examples"] == 3
    assert len(sample_data["examples"]) == 3
    payload = "".join(
        json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in sample_data["examples"]
    ).encode("utf-8")
    assert receipt_data["examples_digest"] == hashlib.sha256(payload).hexdigest()


def test_iterator_consumed_exactly_once():
    rows = list(EQUIV_ROWS)
    counted = {"n": 0}

    def gen():
        for r in rows:
            counted["n"] += 1
            yield ValidEventWithSource(
                event=_validate_settled_dict(r),
                source_file="f.jsonl.gz",
                source_location=f"line:{counted['n']}",
            )

    emitter = ResearchExampleEmitter(None, sample_size=100)
    result = build_research_dataset(
        gen(),
        raw_input_rows=len(rows),
        schema_excluded_rows=0,
        malformed_empty_participant_rows=0,
        emitter=emitter,
    )
    assert counted["n"] == len(rows)  # consumed once, never re-iterated
    assert result.ready is True
    assert result.emitted_examples == len(result.sample)


def test_empty_ledger_research_ready_zero_rows(tmp_path):
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    write_ledger(root, [])
    code, receipt, sample, examples_path = run_audit_research(root, out, [], sample_size=2)
    assert code == 0
    receipt_data = json.loads(receipt.read_text())
    assert receipt_data["status"] == RESEARCH_STATUS
    assert receipt_data["research_ready"] is True
    assert receipt_data["accounting"]["eligible_examples"] == 0
    assert gzip.decompress(examples_path.read_bytes()) == b""
    assert json.loads(sample.read_text())["examples"] == []


def test_exclusion_counter_mapping():
    assert _exclusion_counter_name("NON_FINITE_PROBABILITY") == "excluded_non_finite_probability"
    assert _exclusion_counter_name("INVALID_PROBABILITY") == "excluded_non_finite_probability"
    assert _exclusion_counter_name("UNEXPECTED_DRAW_FOR_TWO_WAY") == "excluded_unexpected_two_way_draw"
    assert _exclusion_counter_name("INVALID_WINNER_INDEX") == "excluded_invalid_winner"
    assert _exclusion_counter_name("SOMETHING_ELSE") == "excluded_other"


def test_source_location_key_numeric_ordering():
    # Numeric line:N / index:N ordering, not lexical ("10" < "2" lexically).
    assert _source_location_key("line:2") < _source_location_key("line:10")
    assert _source_location_key("index:3") < _source_location_key("index:12")
    # Unparseable locations sort after numeric ones.
    assert _source_location_key("line:5") < _source_location_key("weird")


def test_pick_representative_deterministic_no_input_order():
    bare = make_row("hockey:1", event_date="2023-07-01")
    url_only = make_row("hockey:1", event_date="2023-07-01", source_url="https://x/1")
    raw_only = make_row("hockey:1", event_date="2023-07-01", raw_sha256="cd" * 32)
    full = make_row("hockey:1", event_date="2023-07-01", source_url="https://x/1", raw_sha256="cd" * 32)

    def entry(row, src, loc):
        return ValidEventWithSource(event=_validate_settled_dict(row), source_file=src, source_location=loc)

    a = entry(bare, "f", "line:10")
    b = entry(url_only, "f", "line:2")
    c = entry(full, "f", "line:7")
    # Max provenance coverage wins, independent of input order.
    assert _pick_representative([a, b, c]) is c
    assert _pick_representative([c, a, b]) is c
    assert _pick_representative([b, c, a]) is c
    # Tied coverage (one field each) -> stable numeric source tie-break.
    d = entry(url_only, "f", "line:10")
    e = entry(raw_only, "f", "line:2")
    assert _pick_representative([d, e]) is e
    assert _pick_representative([e, d]) is e
    # Same location count, different files -> source_file decides.
    f1 = entry(url_only, "a.json", "line:5")
    f2 = entry(raw_only, "b.json", "line:1")
    assert _pick_representative([f1, f2]) is f1
    assert _pick_representative([f2, f1]) is f1
