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
