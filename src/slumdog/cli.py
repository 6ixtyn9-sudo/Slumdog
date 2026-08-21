from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .backfill import backfill, backfill_sport
from .detail_worker import capture_detail_batch, enrich_events_from_details
from .depth_sweep import run_depth_sweep
from .forebet import ForebetCollector
from .pipeline import parse_capture_receipt, run_from_json
from .reports import render_suggestions
from .settlement import append_settled_from_capture
from .training import train_registry


def main() -> int:
    parser = argparse.ArgumentParser(prog="slumdog")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="freeze all Forebet sport pages")
    capture.add_argument("--date", default=date.today().isoformat())
    capture.add_argument("--root", default=".")
    capture.add_argument("--workers", type=int, default=4)
    capture.add_argument("--sport", action="append", default=None,
                         help="sport key; repeat for multiple (default: all)")

    parse = sub.add_parser("parse", help="normalize captured sport pages")
    parse.add_argument("--date", default=date.today().isoformat())
    parse.add_argument("--root", default=".")

    robbers = sub.add_parser("robbers", help="build every qualifying shadow Robber")
    robbers.add_argument("--events", required=True)
    robbers.add_argument("--date", default=date.today().isoformat())
    robbers.add_argument("--root", default=".")
    robbers.add_argument("--history", default=None, help="settled history JSON")

    sport_history = sub.add_parser("backfill-sport", help="stream one sport's full dated history")
    sport_history.add_argument("--sport", required=True)
    sport_history.add_argument("--start", default=None)
    sport_history.add_argument("--end", required=True)
    sport_history.add_argument("--root", default=".")
    sport_history.add_argument("--workers", type=int, default=6)
    sport_history.add_argument("--batch-size", type=int, default=18)
    sport_history.add_argument("--delay", type=float, default=62.0)

    history = sub.add_parser("backfill", help="bounded historical capture and settlement")
    history.add_argument("--start", required=True)
    history.add_argument("--end", required=True)
    history.add_argument("--root", default=".")
    history.add_argument("--workers", type=int, default=4)
    history.add_argument("--delay", type=float, default=60.0)

    sweep = sub.add_parser("depth-sweep", help="one-shot stratified all-sport audit")
    sweep.add_argument("--date", required=True)
    sweep.add_argument("--root", default=".")
    sweep.add_argument("--per-sport", type=int, default=3)
    sweep.add_argument("--workers", type=int, default=4)
    sweep.add_argument("--relay-pause", type=float, default=62.0)

    details = sub.add_parser("details", help="capture next bounded match-detail batch")
    details.add_argument("--events", required=True)
    details.add_argument("--root", default=".")
    details.add_argument("--max-events", type=int, default=18)
    details.add_argument("--workers", type=int, default=4)

    enrich = sub.add_parser("enrich", help="extract facets from cached details")
    enrich.add_argument("--events", required=True)
    enrich.add_argument("--root", default=".")

    train = sub.add_parser("train", help="train sport models from settled history")
    train.add_argument("--history", required=True)
    train.add_argument("--root", default=".")
    train.add_argument("--min-rows", type=int, default=50)
    train.add_argument("--research-override", action="store_true")

    settle = sub.add_parser("settle", help="append settled rows from a frozen capture")
    settle.add_argument("--date", required=True)
    settle.add_argument("--root", default=".")

    report = sub.add_parser("report", help="render frozen shadow suggestions")
    report.add_argument("--ledger", required=True)
    report.add_argument("--date", default=date.today().isoformat())
    report.add_argument("--root", default=".")

    daily = sub.add_parser("run-daily", help="capture -> parse -> robbers -> suggestions")
    daily.add_argument("--date", default=date.today().isoformat())
    daily.add_argument("--root", default=".")
    daily.add_argument("--workers", type=int, default=4)

    args = parser.parse_args()
    if args.command == "capture":
        collector = ForebetCollector(Path(args.root), workers=args.workers)
        rows = collector.capture_selected(args.date, args.sport)
        print(json.dumps({"date": args.date, "captured": len(rows)}, indent=2))
        return 0
    if args.command == "parse":
        path = parse_capture_receipt(args.date, args.root)
        print(path)
        return 0
    if args.command == "robbers":
        path = run_from_json(args.events, args.date, args.root, history_path=args.history)
        print(path)
        return 0
    if args.command == "backfill-sport":
        path = backfill_sport(
            args.sport, args.end, args.root, args.start,
            args.workers, args.batch_size, args.delay,
        )
        print(path)
        return 0
    if args.command == "backfill":
        path = backfill(args.start, args.end, args.root, args.workers, args.delay)
        print(path)
        return 0
    if args.command == "depth-sweep":
        path = run_depth_sweep(args.date, args.root, args.per_sport, args.workers, args.relay_pause)
        print(path)
        return 0
    if args.command == "details":
        path = capture_detail_batch(args.events, args.root, args.max_events, args.workers)
        print(path)
        return 0
    if args.command == "enrich":
        path = enrich_events_from_details(args.events, args.root)
        print(path)
        return 0
    if args.command == "train":
        path = train_registry(args.history, args.root, args.min_rows, args.research_override)
        print(path)
        return 0
    if args.command == "settle":
        path = append_settled_from_capture(args.date, args.root)
        print(path)
        return 0
    if args.command == "report":
        path = render_suggestions(args.ledger, args.date, args.root)
        print(path)
        return 0
    if args.command == "run-daily":
        root = Path(args.root)
        ForebetCollector(root, workers=args.workers).capture_all(args.date)
        events = parse_capture_receipt(args.date, root)
        history = root / "data" / "interim" / "settled_history.json"
        ledger = run_from_json(
            events, args.date, root,
            history_path=history if history.exists() else None,
        )
        report_path = render_suggestions(ledger, args.date, root)
        print(report_path)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
