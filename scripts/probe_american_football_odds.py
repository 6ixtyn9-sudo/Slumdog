#!/usr/bin/env python3
"""Gently probe upcoming Forebet American-football listings for odds."""
import re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bs4 import BeautifulSoup
from slumdog.forebet import RELAY_BASE, relay_get

ODDS = re.compile(r"(?<![0-9])[+-][0-9]{3,5}(?![0-9])")
DATES = ("2026-09-10", "2026-09-13", "2026-09-14", "2026-09-20", "2026-09-27", "2026-10-04")

def probe(day):
    url = f"https://www.forebet.com/en/american-football/predictions/{day}"
    soup = BeautifulSoup(relay_get(RELAY_BASE + url, timeout=45), "html.parser")
    upcoming = priced = 0
    for row in soup.select("div.rcnt"):
        link = row.select_one("a.tnmscn")
        if not link or "/en/american-football/" not in str(link.get("href") or ""): continue
        result = row.select_one(".lscr_td")
        if result and re.search(r"\d", result.get_text(" ", strip=True)): continue
        upcoming += 1
        values = [x.get_text(" ", strip=True) for x in row.select(".haodd span, .lscrsp")]
        has_price = any(ODDS.search(value) for value in values)
        priced += has_price
        print(f"{day}: {values!r} priced={has_price}")
    return upcoming, priced

def main(argv):
    total = priced = 0
    for day in argv[1:] or DATES:
        try:
            count, found = probe(day); total += count; priced += found
            print(f"{day}: upcoming={count} priced={found}")
        except Exception as exc:
            print(f"{day}: ERROR {type(exc).__name__}: {exc}")
        time.sleep(8)
    print(f"TOTAL upcoming={total} priced={priced}")
    return 0
if __name__ == "__main__": raise SystemExit(main(sys.argv))
