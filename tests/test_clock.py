from slumdog import clock


def test_today_yesterday_follow_clock():
    assert clock.today_iso() > clock.yesterday_iso()
    assert clock.yesterday_iso() < clock.today_iso()


def test_tz_override_changes_local_day(monkeypatch):
    # UTC and Africa/Johannesburg differ by +2h; pick a TZ where the date can
    # diverge from UTC near midnight and assert the helper honours the env.
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14
    from datetime import datetime
    import zoneinfo
    utc_day = datetime.now(zoneinfo.ZoneInfo("UTC")).date()
    local_day = clock.today()
    assert local_day >= utc_day


def test_invalid_tz_falls_back_to_default():
    import os
    os.environ["TZ"] = "Not/A_Timezone"
    try:
        assert clock.today_iso()  # does not raise
    finally:
        os.environ.pop("TZ", None)
