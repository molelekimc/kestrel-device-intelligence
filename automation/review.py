"""Human review of briefing drafts. Only an approved briefing leaves briefings/drafts/.

    python -m automation.review list
    python -m automation.review edit    <draft> --reviewer NAME [--from-file edited.md] [--note ...]
    python -m automation.review approve <draft> --reviewer NAME [--note ...] [--accept-violations]
    python -m automation.review reject  <draft> --reviewer NAME --note "why"

No review action triggers any change to devices or systems; approval only moves the file."""

import argparse
import difflib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from automation import contract, drafts


class ReviewError(RuntimeError):
    pass


def _record(meta: dict, action: str, reviewer: str, note: str | None, violations: list[str]) -> None:
    meta.setdefault("review_history", []).append({
        "action": action, "reviewer": reviewer,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note, "contract_violations": violations})


def _open_for_review(path: Path) -> tuple[dict, str, str]:
    meta, body, appendix = drafts.load(path)
    if meta.get("status") in ("approved", "rejected"):
        raise ReviewError(f"{path.name} is already {meta['status']}.")
    return meta, body, appendix


def edit(path: Path, reviewer: str, note: str | None = None, from_file: Path | None = None) -> list[str]:
    meta, body, appendix = _open_for_review(path)
    if from_file:
        new_body = Path(from_file).read_text()
    else:
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tmp:
            tmp.write(body)
        subprocess.run([*os.environ.get("EDITOR", "vi").split(), tmp.name], check=True)
        new_body = Path(tmp.name).read_text()
        os.unlink(tmp.name)
    changed = sum(1 for line in difflib.unified_diff(body.splitlines(), new_body.splitlines(), lineterm="")
                  if line[:1] in "+-" and not line.startswith(("+++", "---")))
    violations = contract.check(new_body, meta["week"], set(meta.get("critical_codes", [])))
    meta["status"] = "edited"
    meta["contract_violations"] = violations
    _record(meta, "edited", reviewer, f"{changed} lines changed" + (f"; {note}" if note else ""), violations)
    drafts.save(path, meta, new_body, appendix)
    return violations


def approve(path: Path, reviewer: str, note: str | None = None, accept_violations: bool = False) -> Path:
    meta, body, appendix = _open_for_review(path)
    violations = contract.check(body, meta["week"], set(meta.get("critical_codes", [])))
    if violations and not accept_violations:
        raise ReviewError("Draft does not meet the briefing contract:\n  - " + "\n  - ".join(violations) +
                          "\nEdit it, or approve with --accept-violations and a --note explaining why.")
    if violations and not note:
        raise ReviewError("--accept-violations needs a --note explaining why.")
    meta["status"] = "approved"
    meta["approved_by"] = reviewer
    _record(meta, "approved", reviewer, note, violations)
    target = drafts.approved_dir() / path.name
    drafts.save(target, meta, body, appendix)
    path.unlink()
    return target


def reject(path: Path, reviewer: str, note: str) -> None:
    if not note:
        raise ReviewError("Rejecting needs a --note with the reason.")
    meta, body, appendix = _open_for_review(path)
    meta["status"] = "rejected"
    _record(meta, "rejected", reviewer, note, meta.get("contract_violations", []))
    drafts.save(path, meta, body, appendix)


def list_drafts() -> list[tuple[str, str, str, int]]:
    rows = []
    for folder in (drafts.drafts_dir(), drafts.approved_dir()):
        for p in sorted(folder.glob("*.md")):
            meta, _, _ = drafts.load(p)
            rows.append((str(p.relative_to(drafts.BRIEFINGS)), meta["week"], meta["status"],
                         len(meta.get("contract_violations") or [])))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review weekly briefing drafts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("edit", "approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("draft", type=Path)
        p.add_argument("--reviewer", required=True)
        p.add_argument("--note")
        if name == "edit":
            p.add_argument("--from-file", type=Path, help="use this file as the edited briefing instead of $EDITOR")
        if name == "approve":
            p.add_argument("--accept-violations", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            for path, week, status, n in list_drafts():
                print(f"{status:<9} {week}  {path}  ({n} contract violations)")
        elif args.cmd == "edit":
            v = edit(args.draft, args.reviewer, args.note, args.from_file)
            print(f"Marked edited. Contract violations: {len(v)}")
            for x in v:
                print(f"  - {x}")
        elif args.cmd == "approve":
            print(f"Approved: {approve(args.draft, args.reviewer, args.note, args.accept_violations)}")
        elif args.cmd == "reject":
            reject(args.draft, args.reviewer, args.note)
            print("Marked rejected; it stays in briefings/drafts/.")
    except (ReviewError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
