#!/usr/bin/env python3
"""Report which driver-written evidence files the forward-shadow workflow
would actually commit.

Why this exists
---------------
Workflow files in this repository are owner-hand-authored; agent sessions do
not edit ``.github/workflows/*``. That separation has a failure mode: the
driver gains a new artifact type, the workflow's ``git add`` globs are not
extended, and the artifact is produced on the runner and then silently
discarded when the job ends. That is exactly what happened to the daily
refresh: ``selections_delta_*.json`` has been written on every dispatch since
2026-09-22 and never committed, because the persist step's ``find`` list does
not name it.

This script makes that class of gap visible and machine-checkable without
touching the workflow. It reads the persist step's ``find``/``git add`` lines,
expands them, and compares them against
:data:`forward_shadow_batch.PERSISTED_EVIDENCE` — the driver's own declaration
of every small evidence file it can write.

It is a reporting tool: exit code 0 when everything is covered, 1 when
something is not. It never writes anything.

    python scripts/check_workflow_evidence_globs.py
    python scripts/check_workflow_evidence_globs.py --json
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path


DEFAULT_WORKFLOW = Path(".github/workflows/forward_shadow.yml")

_FIND_ROOT = re.compile(r"\bfind\s+(\S+)")
_NAME_PATTERN = re.compile(r"-name\s+'([^']+)'")
_GIT_ADD_PATH = re.compile(r"\bgit\s+add\s+(?:-f\s+)?([^\s|;&]+)")


def parse_persist_rules(workflow_text: str) -> list[tuple[str, str]]:
    """Extract ``(root, filename_glob)`` rules from a workflow's shell lines.

    Two shapes are recognised, both of which the current persist step uses:

    ``find <root> -type f \\( -name 'a' -o -name 'b' \\) | xargs -r git add -f``
        every ``-name`` pattern on that line, rooted at ``<root>``

    ``git add -f data/reports/capture_*.json``
        a direct path glob, split into its directory and filename parts
    """
    rules: list[tuple[str, str]] = []
    for line in workflow_text.splitlines():
        stripped = line.strip()
        if "find " in stripped and "git add" in stripped:
            root_match = _FIND_ROOT.search(stripped)
            if not root_match:
                continue
            root = root_match.group(1)
            for pattern in _NAME_PATTERN.findall(stripped):
                rules.append((root, pattern))
            continue
        if stripped.startswith("git add") or " git add " in stripped:
            for raw in _GIT_ADD_PATH.findall(stripped):
                if raw in ("-f", "."):
                    continue
                path = Path(raw)
                rules.append((str(path.parent), path.name))
    return rules


def is_covered(root: str, filename: str, rules: list[tuple[str, str]]) -> bool:
    """True if some rule's root contains ``root`` and its glob matches."""
    for rule_root, pattern in rules:
        rule_root = rule_root.rstrip("/")
        if not (root == rule_root or root.startswith(rule_root + "/")):
            continue
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


def uncovered_evidence(
    workflow_text: str, evidence: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Evidence entries the workflow's persist step would NOT commit."""
    rules = parse_persist_rules(workflow_text)
    return [entry for entry in evidence if not is_covered(entry[0], entry[1], rules)]


def _load_evidence() -> list[tuple[str, str]]:
    """Import the driver's declaration, repo-root-relative when possible.

    Preferring the ``scripts.forward_shadow_batch`` package path keeps this
    from creating a second copy of the driver module when the test suite has
    already imported it.
    """
    try:
        from scripts.forward_shadow_batch import PERSISTED_EVIDENCE
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from forward_shadow_batch import PERSISTED_EVIDENCE

    return [tuple(entry) for entry in PERSISTED_EVIDENCE]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if not args.workflow.is_file():
        print(f"workflow not found: {args.workflow}", file=sys.stderr)
        return 2
    evidence = _load_evidence()
    missing = uncovered_evidence(args.workflow.read_text(), evidence)

    if args.as_json:
        print(json.dumps({
            "workflow": str(args.workflow),
            "evidence_declared": len(evidence),
            "uncovered": [{"root": r, "filename_glob": f} for r, f in missing],
        }, indent=2, sort_keys=True))
    else:
        print(f"Declared evidence artifacts: {len(evidence)}")
        if not missing:
            print("All declared evidence is covered by the persist step.")
        else:
            print(f"NOT COMMITTED by {args.workflow} ({len(missing)}):")
            for root, filename in missing:
                print(f"  {root}/{filename}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
