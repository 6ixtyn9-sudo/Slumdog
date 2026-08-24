#!/usr/bin/env python3
"""Flag captured pages yielding H2H totals without an H2H marker."""
import glob, hashlib, json, sys
from pathlib import Path
from bs4 import BeautifulSoup
from slumdog.detail_facets import parse_detail
ROOT = Path(__file__).resolve().parents[1]
def main(argv):
    paths = sorted(glob.glob(str(ROOT / "data/interim/*detailed.json")))
    if not paths: print("No detailed events found; run a depth-sweep first."); return 2
    events = json.loads(Path(paths[-1]).read_text()); suspicious = 0
    sports = argv[1:] or ["basketball","tennis","hockey","baseball","rugby","handball","volleyball","mma"]
    for event in events:
        if event.get("sport") not in sports: continue
        eid = str(event.get("event_id") or ""); digest = hashlib.sha256(eid.encode()).hexdigest()[:16]
        path = ROOT / "data/raw/details" / event["sport"] / f"{digest}.html"
        if not path.exists(): continue
        body = path.read_bytes(); facets = parse_detail(body, event["sport"], str(event.get("participant_1") or ""), str(event.get("participant_2") or ""))
        if facets.common.get("h2h_total_games") is None: continue
        soup = BeautifulSoup(body, "html.parser"); text = soup.get_text(" ", strip=True).lower()
        marker = "head to head" in text or bool(soup.select_one(".h2h,.h2h_div,#h2h,[id*=h2h],[class*=h2h]"))
        if not marker: suspicious += 1; print(f"SUSPICIOUS {event['sport']} {eid}")
    print(f"suspicious pages: {suspicious}"); return 1 if suspicious else 0
if __name__ == "__main__": raise SystemExit(main(sys.argv))
