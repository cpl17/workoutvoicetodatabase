#!/usr/bin/env python3
"""Parse transcribed voice memos into structured workout JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lib.config import REPO_ROOT, Config, load_config
from lib.exercises import ExerciseCatalog
from lib.manifest import Manifest, MemoRecord
from lib.parser import ParseResult, create_parser_backend, parsing_requires_openai_key
from lib.review_queue import ReviewQueue


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ParseStageResult:
    parsed: int = 0
    needs_review: int = 0
    skipped_non_workout: int = 0
    errors: list[str] = field(default_factory=list)
    review_items: list[dict[str, Any]] = field(default_factory=list)


def resolve_transcript_path(memo: MemoRecord) -> Path | None:
    if not memo.transcript_path:
        return None
    path = Path(memo.transcript_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve() if path.is_file() else None


def pending_parse_memos(manifest: Manifest, *, force: bool) -> list[MemoRecord]:
    if force:
        candidates = manifest.list_memos(transcribe_status="done")
    else:
        candidates = manifest.list_memos(transcribe_status="done", parse_status="pending")

    pending: list[MemoRecord] = []
    for memo in candidates:
        if memo.parse_status == "skipped" and not force:
            continue
        if resolve_transcript_path(memo) is None:
            print(
                f"warn  skipping {memo.title!r}: transcript not found ({memo.transcript_path})",
                file=sys.stderr,
            )
            continue
        if not force and memo.parse_status not in {"pending", "failed"}:
            continue
        pending.append(memo)
    return pending


def parsed_output_path(memo_id: str, data_dir: Path) -> Path:
    parsed_dir = data_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    return parsed_dir / f"{memo_id}.json"


def parse_log_dir(memo_id: str, data_dir: Path) -> Path:
    log_dir = data_dir / "parse_logs" / memo_id
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def build_parsed_document(
    *,
    memo: MemoRecord,
    transcript: str,
    parse_result: ParseResult,
    config: Config,
    verification_status: str,
) -> dict[str, Any]:
    return {
        "schema_version": config.parsing.schema_version,
        "memo_id": memo.id,
        "session_id": memo.session_id,
        "recorded_at": memo.recorded_at,
        "memo_type": parse_result.memo_type,
        "transcript_raw": transcript,
        "confidence": parse_result.confidence,
        "verification_status": verification_status,
        "entities": {
            "sets": [
                {
                    "exercise_raw": s.exercise_raw,
                    "exercise_id": s.exercise_id,
                    "exercise_display": s.exercise_display,
                    "exercise_confidence": s.exercise_confidence,
                    "weight": s.weight,
                    "weight_unit": s.weight_unit,
                    "weight_confidence": s.weight_confidence,
                    "reps": s.reps,
                    "reps_confidence": s.reps_confidence,
                    "notes": s.notes,
                }
                for s in parse_result.sets
            ],
            "context_notes": parse_result.context_notes,
        },
        "parse_warnings": parse_result.parse_warnings,
        "parse_metadata": {
            "parser_backend": config.parsing.backend,
            "parser_model": config.parsing.model,
            "parsed_at": _utc_now_iso(),
        },
    }


def collect_verification_flags(
    parse_result: ParseResult,
    exercises: ExerciseCatalog,
    config: Config,
) -> list[dict[str, Any]]:
    """Return verification item specs for low-confidence or new exercise mappings."""
    threshold = config.parsing.confidence_threshold
    require_for = set(config.verification.require_for)
    flags: list[dict[str, Any]] = []

    for index, parsed_set in enumerate(parse_result.sets):
        heard = parsed_set.exercise_raw

        if "new_exercise" in require_for and parsed_set.exercise_id is None and heard:
            flags.append(
                {
                    "field": "exercise",
                    "heard": heard,
                    "proposed": {
                        "exercise_id": None,
                        "display": parsed_set.exercise_display or heard,
                        "set_index": index,
                    },
                    "confidence": parsed_set.exercise_confidence,
                }
            )
        elif parsed_set.exercise_id and exercises.get(parsed_set.exercise_id) is None:
            if "new_exercise" in require_for:
                flags.append(
                    {
                        "field": "exercise",
                        "heard": heard,
                        "proposed": {
                            "exercise_id": parsed_set.exercise_id,
                            "display": parsed_set.exercise_display,
                            "set_index": index,
                        },
                        "confidence": parsed_set.exercise_confidence,
                    }
                )

        if (
            "low_confidence_exercise" in require_for
            and parsed_set.exercise_confidence is not None
            and parsed_set.exercise_confidence < threshold
        ):
            flags.append(
                {
                    "field": "exercise",
                    "heard": heard,
                    "proposed": {
                        "exercise_id": parsed_set.exercise_id,
                        "display": parsed_set.exercise_display,
                        "set_index": index,
                    },
                    "confidence": parsed_set.exercise_confidence,
                }
            )

        if (
            "low_confidence_weight" in require_for
            and parsed_set.weight is not None
            and parsed_set.weight_confidence is not None
            and parsed_set.weight_confidence < threshold
        ):
            flags.append(
                {
                    "field": "weight",
                    "heard": heard,
                    "proposed": {
                        "weight": parsed_set.weight,
                        "weight_unit": parsed_set.weight_unit,
                        "set_index": index,
                    },
                    "confidence": parsed_set.weight_confidence,
                }
            )

        if (
            "low_confidence_reps" in require_for
            and parsed_set.reps is not None
            and parsed_set.reps_confidence is not None
            and parsed_set.reps_confidence < threshold
        ):
            flags.append(
                {
                    "field": "reps",
                    "heard": heard,
                    "proposed": {
                        "reps": parsed_set.reps,
                        "set_index": index,
                    },
                    "confidence": parsed_set.reps_confidence,
                }
            )

    return flags


def run_parse_stage(
    manifest: Manifest,
    *,
    config: Config,
    force: bool,
    dry_run: bool,
) -> ParseStageResult:
    result = ParseStageResult()
    parser = create_parser_backend(config)
    exercises = ExerciseCatalog.load()
    review_queue = ReviewQueue.from_config()
    pending = pending_parse_memos(manifest, force=force)

    if dry_run:
        result.parsed = len(pending)
        print(f"[dry-run] Would parse {result.parsed} memo(s)", file=sys.stderr)
        for memo in pending:
            print(f"  {memo.title}", file=sys.stderr)
        return result

    if not pending:
        print("No memos pending parse", file=sys.stderr)
        return result

    for memo in pending:
        transcript_path = resolve_transcript_path(memo)
        if transcript_path is None:
            continue
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        print(f"Parsing {memo.title!r}...", file=sys.stderr)

        try:
            classify = parser.classify(transcript)
            if not classify.is_workout or classify.memo_type == "non_workout":
                if config.non_workout.action == "ignore":
                    manifest.update_status(
                        memo.id,
                        parse_status="skipped",
                        memo_type="non_workout",
                        verification_status="none",
                        clear_error=True,
                    )
                    result.skipped_non_workout += 1
                    print(f"  skipped non-workout memo", file=sys.stderr)
                    continue

            parse_result = parser.extract(
                transcript,
                classify.memo_type,
                exercises,
                default_weight_unit=config.units.default_weight,
            )
            if parse_result.raw_response is not None:
                log_dir = parse_log_dir(memo.id, config.paths.data)
                (log_dir / "extract.json").write_text(
                    json.dumps(parse_result.raw_response, indent=2) + "\n",
                    encoding="utf-8",
                )

            flags = collect_verification_flags(parse_result, exercises, config)
            verification_status = "pending" if flags else "none"
            parse_status = "needs_review" if flags else "done"

            document = build_parsed_document(
                memo=memo,
                transcript=transcript,
                parse_result=parse_result,
                config=config,
                verification_status=verification_status,
            )
            output_path = parsed_output_path(memo.id, config.paths.data)
            output_path.write_text(
                json.dumps(document, indent=2) + "\n",
                encoding="utf-8",
            )

            manifest.update_status(
                memo.id,
                parse_status=parse_status,
                parsed_path=output_path,
                memo_type=parse_result.memo_type,
                parse_backend=config.parsing.backend,
                parse_schema_version=config.parsing.schema_version,
                confidence=parse_result.confidence,
                verification_status=verification_status,
                clear_error=True,
            )

            for flag in flags:
                item = manifest.add_verification_item(
                    memo_id=memo.id,
                    field=str(flag["field"]),
                    proposed_value=flag["proposed"],
                    confidence=flag.get("confidence"),
                )
                review_queue.add(
                    memo_id=memo.id,
                    field=str(flag["field"]),
                    proposed=flag["proposed"],
                    heard=str(flag["heard"]) if flag.get("heard") else None,
                    confidence=flag.get("confidence"),
                    item_id=item.id,
                )
                result.review_items.append(
                    {
                        "id": item.id,
                        "memo_id": memo.id,
                        "field": flag["field"],
                        "proposed": flag["proposed"],
                        "confidence": flag.get("confidence"),
                    }
                )
                result.needs_review += 1

            result.parsed += 1
            print(f"  wrote {output_path.name} ({parse_status})", file=sys.stderr)

        except Exception as exc:
            manifest.update_status(
                memo.id,
                parse_status="failed",
                error_message=str(exc),
            )
            result.errors.append(f"parse {memo.title!r}: {exc}")
            print(f"Error parsing {memo.title!r}: {exc}", file=sys.stderr)
            return result

    print(
        f"Parsed {result.parsed} memo(s); "
        f"{result.needs_review} verification item(s); "
        f"{result.skipped_non_workout} non-workout skipped",
        file=sys.stderr,
    )
    return result


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Parse transcribed voice memos.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-parse memos even when already done",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be parsed without calling the parser",
    )
    args = parser.parse_args()

    config = load_config()
    if not args.dry_run and parsing_requires_openai_key(config) and not os.environ.get("OPENAI_API_KEY"):
        print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
        return 1

    manifest = Manifest.from_config()
    manifest.init_db()
    result = run_parse_stage(
        manifest,
        config=config,
        force=args.force,
        dry_run=args.dry_run,
    )
    if result.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
