#!/usr/bin/env python3
"""Single entry point: export voice memos, then transcribe pending ones."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from export_voice_memos import (
    export_filename,
    export_from_library,
    list_from_library,
    sync_export_to_manifest,
)
from lib.config import load_config
from lib.manifest import Manifest
from transcribe import transcribe, transcription_requires_openai_key
from transcribe_voice_memos import (
    pending_memos,
    resolve_audio_path,
    transcript_path_for_audio,
)


@dataclass
class PipelineResult:
    """Counts and errors from one pipeline run."""

    exported: int = 0
    transcribed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "error" if self.errors else "ok"

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exported": self.exported,
            "transcribed": self.transcribed,
            "errors": self.errors,
        }


def would_export_memo(memo: dict[str, object], output_dir: Path, *, force: bool) -> bool:
    """Return True if export_from_library would copy this memo."""
    source = memo["source"]
    if not isinstance(source, Path) or not source.is_file():
        return False
    title = str(memo["title"])
    recorded_at = memo.get("recorded_at")
    assert recorded_at is None or isinstance(recorded_at, datetime)
    target = output_dir / export_filename(title, recorded_at)
    return force or not target.exists()


def run_export_stage(
    manifest: Manifest,
    output_dir: Path,
    *,
    force: bool,
    dry_run: bool,
    limit: int | None,
) -> PipelineResult:
    """Export memos from Apple's library and sync manifest rows."""
    result = PipelineResult()
    try:
        memos = list_from_library()
    except RuntimeError as exc:
        result.errors.append(str(exc))
        return result

    to_export = memos[:limit] if limit else memos
    if dry_run:
        result.exported = sum(
            1 for memo in to_export if would_export_memo(memo, output_dir, force=force)
        )
        print(f"[dry-run] Would export {result.exported} memo(s)", file=sys.stderr)
        return result

    print(f"Exporting {len(to_export)} memo(s) to {output_dir}...", file=sys.stderr)
    for memo in to_export:
        try:
            export_result = export_from_library(memo, output_dir, force=force)
            if export_result.audio_path:
                sync_export_to_manifest(manifest, memo, export_result.audio_path)
            if export_result.copied:
                result.exported += 1
        except RuntimeError as exc:
            result.errors.append(f"export {memo['title']!r}: {exc}")
            return result

    print(f"Exported {result.exported} file(s)", file=sys.stderr)
    return result


def run_transcribe_stage(
    manifest: Manifest,
    output_dir: Path,
    *,
    force: bool,
    dry_run: bool,
    language: str | None,
    transcribe_backend: str,
    transcribe_model: str,
) -> PipelineResult:
    """Transcribe manifest rows with transcribe_status=pending."""
    result = PipelineResult()
    pending = pending_memos(manifest, force=force)

    if dry_run:
        result.transcribed = len(pending)
        print(f"[dry-run] Would transcribe {result.transcribed} memo(s)", file=sys.stderr)
        for memo in pending:
            audio_path = resolve_audio_path(memo)
            if audio_path:
                print(f"  {audio_path.name}", file=sys.stderr)
        return result

    if not pending:
        print("No memos pending transcription", file=sys.stderr)
        return result

    for memo in pending:
        audio_path = resolve_audio_path(memo)
        if audio_path is None:
            continue
        target = transcript_path_for_audio(audio_path, output_dir)
        print(f"Transcribing {audio_path.name}...", file=sys.stderr)
        try:
            text = transcribe(audio_path, language=language)
            target.write_text(text.strip() + "\n", encoding="utf-8")
        except Exception as exc:
            manifest.update_status(
                memo.id,
                transcribe_status="failed",
                error_message=str(exc),
            )
            result.errors.append(f"transcribe {memo.title!r}: {exc}")
            print(f"Error transcribing {memo.title!r}: {exc}", file=sys.stderr)
            return result

        manifest.update_status(
            memo.id,
            transcribe_status="done",
            transcript_path=target,
            transcribe_backend=transcribe_backend,
            transcribe_model=transcribe_model,
            clear_error=True,
        )
        print(f"Wrote {target.name}", file=sys.stderr)
        result.transcribed += 1

    print(f"Transcribed {result.transcribed} file(s)", file=sys.stderr)
    return result


def merge_results(*parts: PipelineResult) -> PipelineResult:
    """Combine stage results into one pipeline summary."""
    merged = PipelineResult()
    for part in parts:
        merged.exported += part.exported
        merged.transcribed += part.transcribed
        merged.errors.extend(part.errors)
    return merged


def main() -> int:
    """CLI entry point for the export → transcribe pipeline."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run the voice memo pipeline: export from Apple library, then transcribe."
    )
    parser.add_argument(
        "--stage",
        choices=("export", "transcribe", "all"),
        default="all",
        help="Which stage(s) to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would run without copying or transcribing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export / re-transcribe even when already done",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Export at most this many memos (export stage only)",
    )
    args = parser.parse_args()

    config = load_config()
    voice_memos_dir = config.paths.voice_memos
    transcripts_dir = config.paths.transcripts
    voice_memos_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest.from_config()
    manifest.init_db()

    stages: list[PipelineResult] = []

    if args.stage in ("export", "all"):
        print("=== Export ===", file=sys.stderr)
        stages.append(
            run_export_stage(
                manifest,
                voice_memos_dir,
                force=args.force,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        )
        if stages[-1].errors:
            print(json.dumps(merge_results(*stages).to_json()), flush=True)
            return 1

    if args.stage in ("transcribe", "all"):
        if not args.dry_run and transcription_requires_openai_key(config) and not os.environ.get("OPENAI_API_KEY"):
            result = merge_results(*stages)
            result.errors.append("OPENAI_API_KEY is not set")
            print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
            print(json.dumps(result.to_json()), flush=True)
            return 1

        print("=== Transcribe ===", file=sys.stderr)
        stages.append(
            run_transcribe_stage(
                manifest,
                transcripts_dir,
                force=args.force,
                dry_run=args.dry_run,
                language=config.transcription.language,
                transcribe_backend=config.transcription.backend,
                transcribe_model=config.transcription.model,
            )
        )

    result = merge_results(*stages)
    print(
        f"Pipeline {result.status}: exported={result.exported}, "
        f"transcribed={result.transcribed}, errors={len(result.errors)}",
        file=sys.stderr,
    )
    print(json.dumps(result.to_json()), flush=True)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
