#!/usr/bin/env python3
"""Cross-tab disposition and pricing in a compressed MMA history ledger."""
import gzip, json, sys
from collections import Counter
from pathlib import Path

def main(argv):
    if len(argv) != 2 or not Path(argv[1]).exists():
        print("usage: audit_mma_void_priced.py <history_mma.jsonl.gz>", file=sys.stderr); return 2
    cross, examples = Counter(), {}
    with gzip.open(argv[1], "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = (str(row.get("disposition") or "SETTLED"),
                   row.get("odds_1") is not None and row.get("odds_2") is not None)
            cross[key] += 1; examples.setdefault(key, [])
            if len(examples[key]) < 5: examples[key].append(str(row.get("event_id")))
    print(f"{'disposition':<14} {'priced':<8} {'count':>6}   examples")
    for key, count in sorted(cross.items()):
        print(f"{key[0]:<14} {str(key[1]):<8} {count:>6}   {', '.join(examples[key])}")
    overlap = cross.get(("VOID", True), 0)
    print(f"void/priced overlap={overlap}; void total={sum(v for (d,_),v in cross.items() if d == 'VOID')}; priced total={sum(v for (_,p),v in cross.items() if p)}")
    return 0
if __name__ == "__main__": raise SystemExit(main(sys.argv))
