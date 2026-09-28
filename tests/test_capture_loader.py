

def test_the_loader_passes_on_how_the_body_was_written(tmp_path, monkeypatch):
    """The receipt records body_format; not passing it on left the parser
    guessing from bytes, and a wrong guess parses a column capture as
    HTML and yields nothing."""
    import hashlib
    import json

    import slumdog.capture_loader as cl

    seen: dict = {}
    monkeypatch.setattr(cl, "parse_capture",
                        lambda metadata, root=".": seen.update(metadata) or [])
    body = b'{"format": "columns_v1", "columns": {}}'
    body_dir = tmp_path / "data" / "raw" / "volleyball" / "2026-09-29"
    body_dir.mkdir(parents=True)
    (body_dir / "board.txt").write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    rel = "data/raw/volleyball/2026-09-29/board.txt"
    (body_dir / "board.json").write_text(json.dumps({
        "sport": "volleyball", "target_date": "2026-09-29", "sha256": sha,
        "captured_at": "2026-09-29T06:00:00+00:00", "source_url": "u",
        "body_path": rel, "body_format": "columns_v1",
        "route": "relay_columns"}))
    receipt = tmp_path / "data" / "reports" / "capture.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "target_date": "2026-09-29",
        "captured": [{"sport": "volleyball", "target_date": "2026-09-29",
                      "sha256": sha, "body_path": rel,
                      "metadata_path": rel.replace(".txt", ".json"),
                      "captured_at": "2026-09-29T06:00:00+00:00",
                      "source_url": "u", "body_format": "columns_v1",
                      "route": "relay_columns"}]}))
    cl.load_capture_records(target_date="2026-09-29",
                            capture_receipt_path=receipt, repo_root=tmp_path)
    assert seen.get("body_format") == "columns_v1"
    assert seen.get("route") == "relay_columns"
