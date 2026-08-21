import urllib.error

import pytest

from slumdog import forebet
from slumdog.forebet import relay_get


def test_relay_get_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        class Resp:
            def read(self):
                return b"ok"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(forebet, "_sleep_with_jitter", lambda attempt, base=4.0, cap=40.0: None)
    assert relay_get("https://r.jina.ai/x", max_retries=3) == b"ok"
    assert calls["n"] == 3


def test_relay_get_does_not_retry_hard_errors(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(forebet, "_sleep_with_jitter", lambda attempt, base=4.0, cap=40.0: None)
    with pytest.raises(urllib.error.HTTPError):
        relay_get("https://r.jina.ai/x", max_retries=3)
    assert calls["n"] == 1  # no retry on 401


def test_relay_get_gives_up_after_retries(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(request.full_url, 500, "error", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(forebet, "_sleep_with_jitter", lambda attempt, base=4.0, cap=40.0: None)
    with pytest.raises(urllib.error.HTTPError):
        relay_get("https://r.jina.ai/x", max_retries=3)
    assert calls["n"] == 3
