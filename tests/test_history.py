from slumdog.contracts import SettledEvent
import json
from datetime import datetime, timezone

from slumdog.contracts import EventSnapshot
from slumdog.backfill import backfill_sport
from slumdog.forebet import RawCapture
from slumdog.history import HistoryIndex
from slumdog.pipeline import build_shadow_robbers
from slumdog.settlement import append_settled_from_capture, parse_html_settled


SETTLED_HTML = b"""
<html><body><div class='rcnt'>
<span class='shortTag'>WNB</span>
<a class='tnmscn' href='/en/basketball/matches/test/alpha-beta/1'>
<span class='homeTeam'>Alpha</span><span class='awayTeam'>Beta</span>
<span class='date_bah'>19/08/2026 03:00</span></a>
<div class='fprc'><span>36</span><span>64</span></div>
<div class='predict_no'><span class='forepr'><span>2</span></span></div>
<div class='haodd'><span>+150</span><span>-200</span></div>
<div class='predQ'><div class='fj_column'><span>24</span><span>20</span></div></div>
<div class='scoreLnk'><span>FT</span></div>
<div class='lscr_td'><span>93</span><span>86</span></div>
</div></body></html>
"""


def row(day, event_id, winner, p1="Alpha", p2="Beta"):
    return SettledEvent(event_id, "basketball", day, p1, p2, winner, 90, 80,
                        0.4, 0.6, None, 2)


def test_settled_parser_recovers_result_and_prematch_forecast():
    rows = parse_html_settled(SETTLED_HTML, "basketball", "2026-08-19")
    assert len(rows) == 1
    item = rows[0]
    assert item.winner_index == 1
    assert (item.probability_1, item.probability_2) == (0.36, 0.64)
    assert item.odds_1 == 2.5
    assert item.period_scores_1 == (24.0,)


def test_history_context_uses_strictly_earlier_rows():
    rows = [
        row("2026-08-01", "e1", 1),
        row("2026-08-02", "e2", 2),
        row("2026-08-03", "e3", 1),
        row("2026-08-04", "future", 1),
    ]
    h2h, recent_1, recent_2 = HistoryIndex(rows).context(
        "basketball", "2026-08-04", "Alpha", "Beta"
    )
    assert h2h.total_games == 3
    assert (h2h.participant_1_wins, h2h.participant_2_wins) == (2, 1)
    assert recent_1.games == recent_2.games == 3
    assert recent_1.wins == 2
    assert recent_2.wins == 1


def test_prior_history_can_promote_unpriced_high_confidence_robber():
    prior = [
        row(f"2026-08-0{i+1}", f"e{i}", 1 if i < 3 else 2)
        for i in range(5)
    ]
    event = EventSnapshot(
        event_id="future", sport="basketball", event_date="2026-08-10",
        captured_at=datetime.now(timezone.utc).isoformat(), source_url="u",
        participant_1="Alpha", participant_2="Beta",
        probability_1=0.35, probability_2=0.65, forebet_pick=2,
    )
    candidates = build_shadow_robbers([event], history=HistoryIndex(prior))
    assert len(candidates) == 1
    assert candidates[0].participant == "Alpha"
    assert any("H2H" in reason for reason in candidates[0].reasons)
    assert any("Hot" in reason for reason in candidates[0].reasons)


def test_settlement_append_is_idempotent(tmp_path):
    body_path = tmp_path / "data" / "raw" / "basketball" / "2026-08-19" / "body.html"
    body_path.parent.mkdir(parents=True)
    body_path.write_bytes(SETTLED_HTML)
    report = tmp_path / "data" / "reports" / "capture_2026-08-19.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"captured": [{
        "sport": "basketball", "target_date": "2026-08-19",
        "body_path": str(body_path.relative_to(tmp_path)),
    }]}))
    path = append_settled_from_capture("2026-08-19", tmp_path)
    append_settled_from_capture("2026-08-19", tmp_path)
    rows = json.loads(path.read_text())
    assert len(rows) == 1
    assert rows[0]["winner_index"] == 1


def test_streaming_sport_backfill_writes_compressed_history(tmp_path, monkeypatch):
    def fake_fetch(self, sport, day):
        body = tmp_path / "data" / "raw" / sport / day / "body.html"
        meta = body.with_suffix(".json")
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(SETTLED_HTML.replace(b"19/08/2026", day[8:10].encode()+b"/08/2026"))
        return RawCapture(
            sport=sport, target_date=day, captured_at=day+"T00:00:00+00:00",
            source_url="u", relay_url="r", body_format="html", sha256="abc",
            bytes=body.stat().st_size, body_path=str(body.relative_to(tmp_path)),
            metadata_path=str(meta.relative_to(tmp_path)),
        )

    monkeypatch.setattr("slumdog.forebet.ForebetCollector._fetch", fake_fetch)
    manifest_path = backfill_sport(
        "basketball", "2026-08-19", tmp_path,
        start="2026-08-18", workers=2, batch_size=2, delay_seconds=0,
    )
    import gzip
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dates_completed"] == 2
    assert manifest["settled_rows"] == 2
    with gzip.open(tmp_path / manifest["history_file"], "rt") as handle:
        assert len(handle.readlines()) == 2
