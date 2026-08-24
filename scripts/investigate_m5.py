#!/usr/bin/env python3
"""Milestone 5: Historical Integrity Investigation.

This script finds the 6 SCHEMA_MISSING_PARTICIPANT_1 rows and the hockey:278977 double-write
to inspect their shape, source files, and provenance without deleting or guessing.
It produces a JSON report under /tmp/slumdog_investigation_m5.json.
"""

import argparse
import gzip
import json
from pathlib import Path

def _load_json_file_with_source(path: Path):
    raws = []
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except Exception as e:
        return raws, [str(e)]
    if not isinstance(payload, list):
        return raws, ["SCHEMA_NOT_A_LIST"]
    for idx, item in enumerate(payload):
        if isinstance(item, dict):
            raws.append((item, str(path), f"index:{idx}"))
    return raws, []

def _load_jsonl_gz_file_with_source(path: Path):
    raws = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    d = json.loads(stripped)
                    if isinstance(d, dict):
                        raws.append((d, str(path), f"line:{line_no}"))
                except Exception:
                    pass
    except Exception as e:
        return raws, [str(e)]
    return raws, []

def investigate(root_dir: Path, output_path: Path):
    search_roots = [root_dir]
    if (root_dir / "data").exists():
        search_roots.append(root_dir / "data")
    if root_dir == Path(".") or str(root_dir) == ".":
        search_roots.append(Path("data"))

    interim_candidates = []
    gz_candidates = []

    for sr in search_roots:
        ip = sr / "interim" / "settled_history.json"
        if ip.exists() and ip not in interim_candidates:
            interim_candidates.append(ip)
        rp = sr / "reports"
        if rp.exists():
            for gz in sorted(rp.glob("history_*.jsonl.gz")):
                if gz not in gz_candidates:
                    gz_candidates.append(gz)

    missing_participant_1 = []
    hockey_278977 = []

    for p in interim_candidates:
        raws, _ = _load_json_file_with_source(p)
        for d, src, loc in raws:
            p1 = d.get("participant_1", d.get("home"))
            if not p1 or not isinstance(p1, str):
                missing_participant_1.append({"dict": d, "source": src, "location": loc})
            # Check hockey
            if d.get("sport") == "hockey" and str(d.get("event_id")) == "278977":
                hockey_278977.append({"dict": d, "source": src, "location": loc})

    for p in gz_candidates:
        raws, _ = _load_jsonl_gz_file_with_source(p)
        for d, src, loc in raws:
            p1 = d.get("participant_1", d.get("home"))
            if not p1 or not isinstance(p1, str):
                missing_participant_1.append({"dict": d, "source": src, "location": loc})
            if d.get("sport") == "hockey" and str(d.get("event_id")) == "278977":
                hockey_278977.append({"dict": d, "source": src, "location": loc})

    # Check history_hockey.json container accounting
    hockey_accounting = {}
    for sr in search_roots:
        hj = sr / "reports" / "history_hockey.json"
        if hj.exists():
            try:
                # Read just enough to get accounting
                text = hj.read_text(encoding="utf-8")[:2000000]
                if len(text) < 1900000:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        hockey_accounting = {k: v for k, v in data.items() if not isinstance(v, list)}
            except Exception:
                pass

    report = {
        "missing_participant_1": missing_participant_1,
        "hockey_278977": hockey_278977,
        "hockey_accounting": hockey_accounting
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    print(f"Investigation report written to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data")
    parser.add_argument("--output", default="/tmp/slumdog_investigation_m5.json")
    args = parser.parse_args()
    investigate(Path(args.root), Path(args.output))
