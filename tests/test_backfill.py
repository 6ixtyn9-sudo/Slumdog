import json
import urllib.error

import pytest

from slumdog import backfill as backfill_mod
from slumdog.backfill import _validated_ledger_payloads, backfill_sport
from slumdog.contracts import SettledEvent


def _http_422(day, sport="handball"):
    return urllib.error.HTTPError(
        url=f"https://r.jina.ai/https://www.forebet.com/en/{sport}/predictions/{day}",
        code=422, msg="Unprocessable Entity", hdrs=None, fp=None,
    )


def test_relay_422_on_non_football_is_covered_empty_day(tmp_path, monkeypatch):
    # Off-season dates cause the relay to 422 ("no listing"). These must be
    # recorded as covered empty days, NOT retried forever as failures.
    calls = []

    def fake_fetch(self, sport, day):
        calls.append(day)
        raise _http_422(day, sport)

    monkeypatch.setattr(backfill_mod.ForebetCollector, "_fetch", fake_fetch)

    manifest_path = backfill_sport(
        "handball", root=tmp_path,
        start="2025-08-11", end="2025-08-13",
        workers=1, batch_size=1, delay_seconds=0,
    )
    manifest = json.loads(manifest_path.read_text())

    assert manifest["dates_requested"] == 3
    assert manifest["dates_completed"] == 3
    assert manifest["settled_rows"] == 0
    assert manifest["empty_days"] == 3
    assert manifest["failures"] == []
    empty_dates = {r["date"] for r in manifest["daily_receipts"] if r.get("empty")}
    assert empty_dates == {"2025-08-11", "2025-08-12", "2025-08-13"}

    # Second run skips the already-covered empty dates entirely (no re-fetch).
    calls.clear()
    backfill_sport(
        "handball", root=tmp_path,
        start="2025-08-11", end="2025-08-13",
        workers=1, batch_size=1, delay_seconds=0,
    )
    assert calls == []


def test_422_treatment_excludes_football(tmp_path, monkeypatch):
    # Football uses the JSON endpoint which returns an empty list, not 422.
    # A 422 there must stay a real failure rather than be swallowed.
    def fake_fetch(self, sport, day):
        raise _http_422(day, sport)

    monkeypatch.setattr(backfill_mod.ForebetCollector, "_fetch", fake_fetch)
    manifest_path = backfill_sport(
        "football", root=tmp_path,
        start="2025-09-21", end="2025-09-21",
        workers=1, batch_size=1, delay_seconds=0,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dates_completed"] == 0
    assert manifest["empty_days"] == 0
    assert len(manifest["failures"]) == 1
    assert "2025-09-21" in manifest["failures"][0]


def test_non_422_error_still_retried(tmp_path, monkeypatch):
    def fake_fetch(self, sport, day):
        raise RuntimeError("boom")

    monkeypatch.setattr(backfill_mod.ForebetCollector, "_fetch", fake_fetch)
    manifest_path = backfill_sport(
        "rugby", root=tmp_path,
        start="2025-06-17", end="2025-06-17",
        workers=1, batch_size=1, delay_seconds=0,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dates_completed"] == 0
    assert manifest["empty_days"] == 0
    assert any("RuntimeError" in f for f in manifest["failures"])


def _settled(score_1=2.0, score_2=1.0):
    return SettledEvent(
        event_id="hockey:7", sport="hockey", event_date="2026-01-02",
        participant_1="Home", participant_2="Away", winner_index=1,
        score_1=score_1, score_2=score_2, probability_1=0.6,
        probability_2=0.4, draw_probability=None, forebet_pick=1,
    )


def test_ledger_payload_validation_collapses_exact_duplicate():
    payloads = _validated_ledger_payloads(
        [_settled(), _settled()], "hockey", "abc123"
    )
    assert len(payloads) == 1
    assert payloads[0]["facets"]["raw_sha256"] == "abc123"


def test_ledger_payload_validation_rejects_conflict_before_write(tmp_path):
    ledger = tmp_path / "history_hockey.jsonl"
    with pytest.raises(ValueError, match=(
        r"sport=hockey date=2026-01-02 event_id=hockey:7; "
        r"differing_fields=.*score_1"
    )):
        payloads = _validated_ledger_payloads(
            [_settled(), _settled(score_1=0.0, score_2=4.0)],
            "hockey", "abc123",
        )
        with ledger.open("a", encoding="utf-8") as output:
            for payload in payloads:
                output.write(json.dumps(payload) + "\n")
    assert not ledger.exists()
