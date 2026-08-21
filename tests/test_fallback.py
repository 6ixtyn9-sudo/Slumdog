import pytest

from slumdog import forebet


def test_fetch_with_fallback_uses_relay_when_it_works(monkeypatch):
    monkeypatch.setattr(forebet, "relay_get", lambda url, timeout=45, max_retries=3: b"relay-body")
    body, route = forebet.fetch_with_fallback("relay", "direct")
    assert route == "relay"
    assert body == b"relay-body"


def test_fetch_with_fallback_falls_back_to_direct(monkeypatch):
    def bad_relay(url, timeout=45, max_retries=3):
        raise RuntimeError("relay auth-walled")

    monkeypatch.setattr(forebet, "relay_get", bad_relay)
    monkeypatch.setattr(forebet, "direct_get", lambda url, timeout=40, max_retries=3: b"direct-body")
    body, route = forebet.fetch_with_fallback("relay", "direct")
    assert route == "direct"
    assert body == b"direct-body"


def test_fetch_with_fallback_fails_fast_on_github_runner(monkeypatch):
    def bad_relay(url, timeout=45, max_retries=3):
        raise RuntimeError("relay auth-walled")

    monkeypatch.setattr(forebet, "relay_get", bad_relay)
    monkeypatch.setattr(forebet, "on_github_runner", lambda: True)
    direct_called = {"n": 0}
    monkeypatch.setattr(
        forebet, "direct_get",
        lambda url, timeout=40, max_retries=3: direct_called.__setitem__("n", direct_called["n"] + 1) or b"direct",
    )
    with pytest.raises(RuntimeError, match="relay auth-walled"):
        forebet.fetch_with_fallback("relay", "direct")
    assert direct_called["n"] == 0  # direct never attempted on a runner


def test_direct_get_raises_when_all_transports_fail(monkeypatch):
    def bad(url, timeout):
        raise RuntimeError("nope")

    monkeypatch.setattr(forebet, "_urllib_get", bad)
    # curl_cffi is optional; simulate it being unavailable.
    monkeypatch.setattr(forebet, "_CFFI_IMPERSONATIONS", ())
    with pytest.raises(RuntimeError, match="across transports"):
        forebet.direct_get("https://www.forebet.com/x", max_retries=1)


def test_relay_get_markdown_unwraps_reader_wrapper(monkeypatch):
    url = "https://r.jina.ai/https://www.forebet.com/scripts/getrs.php?in=2026-08-19"
    expected = "https://www.forebet.com/scripts/getrs.php?in=2026-08-19"
    wrapped = (
        b"Title: \n\n"
        b"URL Source: https://www.forebet.com/scripts/getrs.php?in=2026-08-19\n\n"
        b"Markdown Content:\n"
        b'[[{"id":"1","Host_SC":null}]]'
    )
    monkeypatch.setattr(
        forebet, "relay_get_markdown",
        lambda url, expected_url, timeout=45, max_retries=3: wrapped.split(b"Markdown Content:\n", 1)[1],
    )
    body = forebet.relay_get_markdown(url, expected)
    assert body.startswith(b"[[{")
    assert b"Markdown Content" not in body


def test_route_recorded_in_raw_capture(monkeypatch, tmp_path):
    def fake_fallback(relay_url, direct_url, timeout=45, max_retries=3):
        return b"<html>not really used</html>", "direct"

    monkeypatch.setattr(forebet, "fetch_with_fallback", fake_fallback)
    monkeypatch.setattr(forebet, "validate_capture_body", lambda *a, **k: None)
    monkeypatch.setattr(
        forebet.ForebetCollector,
        "_fetch",
        lambda self, sport, day: forebet.RawCapture(
            sport=sport, target_date=day, captured_at=day + "T00:00:00+00:00",
            source_url="s", relay_url="r", body_format="html", sha256="abc",
            bytes=10, body_path="x", metadata_path="y", route="direct",
        ),
    )
    collector = forebet.ForebetCollector(tmp_path)
    cap = collector._fetch("football", "2026-08-19")
    assert cap.route == "direct"
