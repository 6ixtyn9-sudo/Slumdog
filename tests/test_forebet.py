import json

import pytest

from slumdog.forebet import (
    CONTENT_MARKER,
    ForebetCollector,
    source_url,
    unwrap_reader,
    validate_html_body,
)
from slumdog.sports import SPORTS


def test_source_urls_are_date_addressable():
    for spec in SPORTS.values():
        url = source_url(spec, "2026-08-22")
        assert url.startswith("https://www.forebet.com/")
        if not spec.current_only:
            assert "2026-08-22" in url


def test_reader_wrapper_requires_exact_provenance():
    source = "https://www.forebet.com/en/tennis/predictions/2026-08-22"
    body = b"real body data that is long enough"
    raw = f"Title: Tennis\n\nURL Source: {source}\n\n{CONTENT_MARKER}".encode() + body
    assert unwrap_reader(raw, source) == body
    with pytest.raises(ValueError, match="source URL mismatch"):
        unwrap_reader(raw, source + "?other=1")


def test_reader_rejects_forebet_404_content():
    source = "https://www.forebet.com/missing"
    raw = (
        f"URL Source: {source}\n\n{CONTENT_MARKER}"
        "## Not what you were looking for?\nForebet 404 Error"
    ).encode()
    with pytest.raises(ValueError, match="404 content"):
        unwrap_reader(raw, source)


def test_html_validation_rejects_false_success_and_accepts_sport_date():
    valid = b"<html>Basketball predictions for 22/08/2026" + b"x" * 100
    validate_html_body(valid, "basketball", "2026-08-22")
    with pytest.raises(ValueError, match="404"):
        validate_html_body(b"<html>Not what you were looking for?" + b"x" * 100,
                           "basketball", "2026-08-22")


def test_capture_all_is_fail_soft_and_writes_receipt(tmp_path, monkeypatch):
    collector = ForebetCollector(tmp_path, workers=2)

    def fake_fetch(sport, target_date):
        if sport == "mma":
            raise RuntimeError("temporary")
        directory = tmp_path / "data" / "raw" / sport / target_date
        directory.mkdir(parents=True, exist_ok=True)
        body = directory / "body.txt"
        meta = directory / "body.json"
        body.write_text("raw")
        from slumdog.forebet import RawCapture
        return RawCapture(sport, target_date, "2026-08-22T00:00:00+00:00", "u", "r", "html", "abc", 3,
                          str(body.relative_to(tmp_path)), str(meta.relative_to(tmp_path)))

    monkeypatch.setattr(collector, "_fetch", fake_fetch)
    rows = collector.capture_all("2026-08-22")
    assert len(rows) == len(SPORTS) - 1
    receipt = json.loads((tmp_path / "data" / "reports" / "capture_2026-08-22.json").read_text())
    assert receipt["failures"] == ["mma:RuntimeError:temporary"]


def test_football_validation_parses_html_wrapped_payload():
    from slumdog.forebet import validate_football_json_body
    body = b'<html><body>[[{"id":"1","Pred_1":"50","Pred_2":"50"}]]</body></html>'
    validate_football_json_body(body)


def test_football_validation_rejects_truncated_html_wrapped_json():
    from slumdog.forebet import validate_football_json_body
    body = b'<html><body>[[{"id":"1","Pred_1":"50"},{"id":"2"'
    with pytest.raises(ValueError, match="football JSON body missing"):
        validate_football_json_body(body)


def test_football_fetch_retries_truncated_then_succeeds(tmp_path, monkeypatch):
    from slumdog import forebet as forebet_mod
    good = b'<html><body>[[{"id":"1","Pred_1":"50","Pred_2":"50"}]]</body></html>'
    bad = b'<html><body>[[{"id":"1","Pred_1":"50"},{"id":"2"'
    responses = [bad, good]

    def fake_relay_markdown(relay, target, timeout):
        return responses.pop(0)

    monkeypatch.setattr(forebet_mod, "relay_get_markdown", fake_relay_markdown)
    monkeypatch.setattr(forebet_mod, "_sleep_with_jitter", lambda *a, **k: None)
    cap = forebet_mod.ForebetCollector(tmp_path)._fetch("football", "2025-09-21")
    assert cap.bytes == len(good)
    assert (tmp_path / cap.body_path).read_bytes() == good


class TestCaptureSelectedRefreshParams:
    def test_receipt_name_validation(self, tmp_path):
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        with pytest.raises(ValueError, match="bad receipt_name"):
            collector.capture_selected(
                "2026-09-23", sports=["hockey"],
                receipt_name="capture_refresh_2026-09-23/evil.json")
        with pytest.raises(ValueError, match="bad receipt_name"):
            collector.capture_selected(
                "2026-09-23", sports=["hockey"],
                receipt_name="capture_refresh_2026-09-23.txt")

    def test_force_skips_raw_dir_reuse(self, tmp_path, monkeypatch):
        """force=True must re-fetch even when a same-date raw capture dir
        exists (the refresh needs a genuinely fresh snapshot)."""
        from slumdog.forebet import ForebetCollector
        collector = ForebetCollector(root=tmp_path, timeout=5, workers=1)
        raw = tmp_path / "data" / "raw" / "hockey" / "2026-09-23"
        raw.mkdir(parents=True, exist_ok=True)
        # A valid prior capture pair (sidecar metadata is what reuses).
        (raw / "prior.json").write_text(json.dumps({
            "sport": "hockey", "target_date": "2026-09-23",
            "captured_at": "2026-09-20T04:00:00Z",
            "source_url": "https://example.invalid/x",
            "relay_url": "https://relay.invalid/x",
            "body_format": "html", "sha256": "0" * 64, "bytes": 3,
            "body_path": "data/raw/hockey/2026-09-23/prior.txt",
            "metadata_path": "data/raw/hockey/2026-09-23/prior.json",
            "route": "direct",
        }))

        calls = []

        def _fake_fetch(self, sport, target_date):
            calls.append((sport, target_date))
            raise RuntimeError("offline test: no network")

        monkeypatch.setattr(
            "slumdog.forebet.ForebetCollector._fetch", _fake_fetch)

        # Without force: reuse — the fetch seam stays untouched.
        captures = collector.capture_selected("2026-09-23", sports=["hockey"])
        assert len(captures) == 1 and captures[0].sport == "hockey"
        assert calls == []
        # With force: the raw dir no longer shields the sport from a fetch.
        collector.capture_selected("2026-09-23", sports=["hockey"], force=True)
        assert calls == [("hockey", "2026-09-23")]
