import json

from slumdog import detail_worker


def test_stratified_detail_capture_balances_sports(tmp_path, monkeypatch):
    events = []
    for sport in ("football", "basketball", "tennis"):
        for i in range(5):
            events.append({
                "event_id": f"{sport}:{i}",
                "sport": sport,
                "source_url": f"https://www.forebet.com/en/{sport}/matches/x/{i}",
                "probability_1": 0.30 + i * 0.01,
                "probability_2": 0.70 - i * 0.01,
            })
    path = tmp_path / "events.json"
    path.write_text(json.dumps(events))
    monkeypatch.setattr(detail_worker, "_fetch_detail", lambda _url: b"<html>" + b"x" * 100)

    report_path = detail_worker.capture_stratified_details(
        path, tmp_path, per_sport=2, workers=3, delay_seconds=0,
    )
    report = json.loads(report_path.read_text())
    assert report["requested"] == 6
    assert {sport: 2 for sport in ("football", "basketball", "tennis")} == {
        sport: sum(item["sport"] == sport for item in report["results"])
        for sport in ("football", "basketball", "tennis")
    }
    assert all(item["status"] == "OK" for item in report["results"])
