#!/usr/bin/env python3
"""Interactive CLI for reviewing low-confidence parse results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lib.config import REPO_ROOT, load_config
from lib.exercises import ExerciseCatalog, slugify
from lib.manifest import Manifest
from lib.review_queue import ReviewQueue


def _load_parsed_document(memo_id: str) -> dict[str, Any] | None:
    config = load_config()
    path = config.paths.data / "parsed" / f"{memo_id}.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _read_transcript(manifest: Manifest, memo_id: str) -> str | None:
    memo = manifest.get_memo(memo_id)
    if memo is None or not memo.transcript_path:
        return None
    path = Path(memo.transcript_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def cmd_list(queue: ReviewQueue) -> int:
    items = queue.list_items(status="pending")
    if not items:
        print("No pending review items.")
        return 0
    for item in items:
        conf = f"{item.confidence:.2f}" if item.confidence is not None else "n/a"
        print(f"{item.id}  memo={item.memo_id}  field={item.field}  confidence={conf}")
    return 0


def cmd_show(queue: ReviewQueue, manifest: Manifest, item_id: str) -> int:
    item = queue.get(item_id)
    if item is None:
        print(f"Review item not found: {item_id}", file=sys.stderr)
        return 1

    print(json.dumps(item.to_dict(), indent=2))
    transcript = _read_transcript(manifest, item.memo_id)
    if transcript:
        print("\nTranscript:")
        print(transcript)
    parsed = _load_parsed_document(item.memo_id)
    if parsed:
        print("\nParsed JSON:")
        print(json.dumps(parsed, indent=2))
    return 0


def _approve_exercise(
    catalog: ExerciseCatalog,
    manifest: Manifest,
    proposed: dict[str, Any],
    heard: str | None,
) -> None:
    exercise_id = proposed.get("exercise_id")
    display = str(proposed.get("display") or heard or "Unknown Exercise")
    aliases = [heard] if heard else []
    if exercise_id:
        exercise_id = str(exercise_id)
        if catalog.get(exercise_id) is None:
            catalog.add_candidate(
                display_name=display,
                aliases=aliases or [display],
                source="user_verified",
                exercise_id=exercise_id,
            )
        catalog.approve(exercise_id, extra_aliases=aliases)
        manifest.upsert_exercise_alias(
            canonical_id=exercise_id,
            alias=heard or display,
            source="user_verified",
        )
    else:
        exercise_id = slugify(display)
        catalog.add_candidate(
            display_name=display,
            aliases=aliases or [display],
            source="user_verified",
            exercise_id=exercise_id,
        )
        catalog.approve(exercise_id, extra_aliases=aliases)
        manifest.upsert_exercise_alias(
            canonical_id=exercise_id,
            alias=heard or display,
            source="user_verified",
        )


def cmd_approve(
    queue: ReviewQueue,
    manifest: Manifest,
    catalog: ExerciseCatalog,
    item_id: str,
) -> int:
    item = queue.get(item_id)
    if item is None:
        print(f"Review item not found: {item_id}", file=sys.stderr)
        return 1
    if item.status != "pending":
        print(f"Item {item_id} is already {item.status}", file=sys.stderr)
        return 1

    if item.field == "exercise" and isinstance(item.proposed, dict):
        _approve_exercise(catalog, manifest, item.proposed, item.heard)
        catalog.save()

    manifest.resolve_verification_item(
        item_id,
        status="approved",
        resolved_value=item.proposed,
        resolved_via="cli",
    )
    queue.update_status(item_id, "approved")

    pending = manifest.list_verification_items(status="pending", memo_id=item.memo_id)
    if not pending:
        manifest.update_status(item.memo_id, verification_status="approved")

    print(f"Approved {item_id}")
    return 0


def cmd_correct(
    queue: ReviewQueue,
    manifest: Manifest,
    catalog: ExerciseCatalog,
    item_id: str,
    value_json: str,
) -> int:
    item = queue.get(item_id)
    if item is None:
        print(f"Review item not found: {item_id}", file=sys.stderr)
        return 1
    if item.status != "pending":
        print(f"Item {item_id} is already {item.status}", file=sys.stderr)
        return 1

    try:
        corrected = json.loads(value_json)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON value: {exc}", file=sys.stderr)
        return 1

    if item.field == "exercise" and isinstance(corrected, dict):
        _approve_exercise(catalog, manifest, corrected, item.heard)
        catalog.save()

    manifest.resolve_verification_item(
        item_id,
        status="corrected",
        resolved_value=corrected,
        resolved_via="cli",
    )
    queue.update_status(item_id, "approved")

    pending = manifest.list_verification_items(status="pending", memo_id=item.memo_id)
    if not pending:
        manifest.update_status(item.memo_id, verification_status="approved")

    print(f"Corrected {item_id}")
    return 0


def cmd_reject(queue: ReviewQueue, manifest: Manifest, item_id: str) -> int:
    item = queue.get(item_id)
    if item is None:
        print(f"Review item not found: {item_id}", file=sys.stderr)
        return 1
    if item.status != "pending":
        print(f"Item {item_id} is already {item.status}", file=sys.stderr)
        return 1

    manifest.resolve_verification_item(
        item_id,
        status="rejected",
        resolved_via="cli",
    )
    queue.update_status(item_id, "rejected")

    pending = manifest.list_verification_items(status="pending", memo_id=item.memo_id)
    if not pending:
        manifest.update_status(item.memo_id, verification_status="rejected")

    print(f"Rejected {item_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Review low-confidence parse results.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List pending review items")

    show_parser = subparsers.add_parser("show", help="Show one review item with context")
    show_parser.add_argument("--id", required=True)

    approve_parser = subparsers.add_parser("approve", help="Accept the proposed value")
    approve_parser.add_argument("--id", required=True)

    correct_parser = subparsers.add_parser("correct", help="Approve with a corrected value")
    correct_parser.add_argument("--id", required=True)
    correct_parser.add_argument("--value", required=True, help="JSON object with corrected value")

    reject_parser = subparsers.add_parser("reject", help="Reject the proposed value")
    reject_parser.add_argument("--id", required=True)

    args = parser.parse_args()

    manifest = Manifest.from_config()
    manifest.init_db()
    queue = ReviewQueue.from_config()
    catalog = ExerciseCatalog.load()

    if args.command == "list":
        return cmd_list(queue)
    if args.command == "show":
        return cmd_show(queue, manifest, args.id)
    if args.command == "approve":
        return cmd_approve(queue, manifest, catalog, args.id)
    if args.command == "correct":
        return cmd_correct(queue, manifest, catalog, args.id, args.value)
    if args.command == "reject":
        return cmd_reject(queue, manifest, args.id)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
