import json

import pytest

from slumdog import forebet
from slumdog.forebet import ForebetCollector


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
    # Owner finding, 2026-09-28 (direct-vs-relay probe): direct is now
    # attempted EVERY time relay fails, MEASURED this run, on every
    # network — a static on_github_runner() skip went stale silently.
    body, route = forebet.fetch_with_fallback("relay", "direct")
    assert route == "direct"
    assert body == b"direct-body"


def test_fetch_with_fallback_now_tries_direct_even_on_a_github_runner(monkeypatch):
    # Owner finding, 2026-09-28 (direct-vs-relay probe, run 36470920157):
    # skipping direct on a GitHub runner was a hardcoded assumption from a
    # measurement weeks old that went stale silently. Direct must now be
    # attempted regardless of on_github_runner()'s answer.
    def bad_relay(url, timeout=45, max_retries=3):
        raise RuntimeError("relay auth-walled")

    monkeypatch.setattr(forebet, "relay_get", bad_relay)
    monkeypatch.setattr(forebet, "on_github_runner", lambda: True)
    direct_called = {"n": 0, "max_retries": None}

    def fake_direct(url, timeout=40, max_retries=3):
        direct_called["n"] += 1
        direct_called["max_retries"] = max_retries
        return b"direct-body"

    monkeypatch.setattr(forebet, "direct_get", fake_direct)
    body, route = forebet.fetch_with_fallback("relay", "direct")
    assert route == "direct"
    assert body == b"direct-body"
    assert direct_called["n"] == 1
    # Bounded to a single round regardless of this call's own max_retries
    # (a bad run should cost one extra request, not repeat the relay's
    # retry budget).
    assert direct_called["max_retries"] == 1


def test_fetch_with_fallback_names_both_failures_when_both_paths_are_down(monkeypatch):
    # The outcome must stay visible (receipt/failures list) rather than
    # silently collapsing to "direct wasn't even tried" or losing the
    # direct-side reason entirely.
    def bad_relay(url, timeout=45, max_retries=3):
        raise RuntimeError("relay auth-walled")

    def bad_direct(url, timeout=40, max_retries=3):
        raise ValueError("direct also refused")

    monkeypatch.setattr(forebet, "relay_get", bad_relay)
    monkeypatch.setattr(forebet, "direct_get", bad_direct)
    with pytest.raises(RuntimeError) as excinfo:
        forebet.fetch_with_fallback("relay", "direct")
    message = str(excinfo.value)
    assert "relay auth-walled" in message
    assert "direct also refused" in message
    assert "measured, not assumed" in message



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


def test_capture_selected_reuses_existing_same_day_capture(monkeypatch, tmp_path):
    from slumdog.forebet import RawCapture

    fetch_calls = {"n": 0}
    def fake_fetch(self, sport, day):
        fetch_calls["n"] += 1
        day_dir = tmp_path / "data" / "raw" / sport / day
        day_dir.mkdir(parents=True, exist_ok=True)
        cap = RawCapture(
            sport=sport, target_date=day, captured_at=day + "T00:00:00+00:00",
            source_url="s", relay_url="r", body_format="html", sha256="abc",
            bytes=10, body_path="x", metadata_path="y", route="relay",
        )
        (day_dir / "meta.json").write_text(json.dumps({
            "sport": sport, "target_date": day, "captured_at": day + "T00:00:00+00:00",
            "source_url": "s", "relay_url": "r", "body_format": "html", "sha256": "abc",
            "bytes": 10, "body_path": "x", "metadata_path": "y", "route": "relay",
        }))
        return cap

    monkeypatch.setattr(ForebetCollector, "_fetch", fake_fetch)
    # Pre-existing, unrelated to this file's fetch_with_fallback coverage:
    # capture_selected also fetches football's extra-market sidecar via a
    # real relay_get_markdown call unless mocked, which — depending on
    # what the relay actually returns this moment — can retry with
    # multi-second backoff and turn this into a real-network-dependent,
    # potentially very slow test. Kept a no-op here so this test measures
    # only what it says it measures (fetch/reuse counting), deterministically.
    monkeypatch.setattr(
        "slumdog.forebet.fetch_football_markets", lambda *a, **k: None)
    c = ForebetCollector(tmp_path)
    # First call fetches all sports; second call reuses existing (no re-fetch).
    c.capture_selected("2026-08-19", ["football", "basketball"])
    first = fetch_calls["n"]
    c.capture_selected("2026-08-19", ["football", "basketball"])
    assert fetch_calls["n"] == first  # nothing re-fetched


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
