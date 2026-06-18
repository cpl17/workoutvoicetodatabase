#!/usr/bin/env python3
"""Batch-transcribe audio files from voice-memos/ to transcripts/.

Uses the SQLite manifest to find memos with transcribe_status=pending,
then calls transcribe.py per file and updates the manifest.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from lib.config import REPO_ROOT, load_config
from lib.manifest import Manifest, MemoRecord
from transcribe import transcribe, transcription_requires_openai_key


def resolve_audio_path(memo: MemoRecord) -> Path | None:
    """Resolve a manifest audio_path to an on-disk file."""
    if not memo.audio_path:
        return None
    path = Path(memo.audio_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve() if path.is_file() else None


def transcript_path_for_audio(audio_path: Path, output_dir: Path) -> Path:
    """Map voice-memos/foo.m4a → transcripts/foo.txt (same stem, .txt extension)."""
    return output_dir / f"{audio_path.stem}.txt"


def pending_memos(manifest: Manifest, *, force: bool) -> list[MemoRecord]:
    """Return manifest rows that should be transcribed."""
    if force:
        candidates = manifest.list_memos()
    else:
        candidates = manifest.list_memos(transcribe_status="pending")

    pending: list[MemoRecord] = []
    for memo in candidates:
        if memo.transcribe_status == "skipped" and not force:
            continue
        if resolve_audio_path(memo) is None:
            print(
                f"warn  skipping {memo.title!r}: audio not found ({memo.audio_path})",
                file=sys.stderr,
            )
            continue
        if not force and memo.transcribe_status != "pending":
            continue
        pending.append(memo)
    return pending


def main() -> int:
    """CLI entry point: transcribe pending voice memos, or list them with --dry-run."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Transcribe new voice memos from a folder with Whisper."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        help="Folder with audio files (default: paths.voice_memos from config.yaml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Folder for transcript .txt files (default: paths.transcripts from config.yaml)",
    )
    parser.add_argument(
        "-l",
        "--language",
        help="ISO-639-1 language code (e.g. en). Optional; Whisper auto-detects if omitted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transcribe even if manifest transcribe_status is already done",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be transcribed without calling Whisper",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not read or write data/manifest.db",
    )
    args = parser.parse_args()

    config = load_config()

    if not args.dry_run and transcription_requires_openai_key(config) and not os.environ.get("OPENAI_API_KEY"):
        print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
        return 1

    input_dir = (args.input or config.paths.voice_memos).expanduser().resolve()
    output_dir = (args.output or config.paths.transcripts).expanduser().resolve()

    if not input_dir.is_dir():
        print(f"Error: input folder not found: {input_dir}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: Manifest | None = None
    if not args.no_manifest:
        manifest = Manifest.from_config()
        manifest.init_db()

    if manifest is None:
        print("Error: manifest is required; omit --no-manifest", file=sys.stderr)
        return 1

    pending = pending_memos(manifest, force=args.force)
    if not pending:
        print(f"No new voice memos to transcribe in {input_dir}", file=sys.stderr)
        return 0

    if args.dry_run:
        for memo in pending:
            audio_path = resolve_audio_path(memo)
            if audio_path:
                print(audio_path.name)
        return 0

    transcribed = 0
    for memo in pending:
        audio_path = resolve_audio_path(memo)
        if audio_path is None:
            continue
        target = transcript_path_for_audio(audio_path, output_dir)
        print(f"Transcribing {audio_path.name}...", file=sys.stderr)
        try:
            text = transcribe(audio_path, language=args.language)
            target.write_text(text.strip() + "\n", encoding="utf-8")
        except Exception as exc:
            manifest.update_status(
                memo.id,
                transcribe_status="failed",
                error_message=str(exc),
            )
            print(f"Error transcribing {memo.title!r}: {exc}", file=sys.stderr)
            return 1

        manifest.update_status(
            memo.id,
            transcribe_status="done",
            transcript_path=target,
            transcribe_backend=config.transcription.backend,
            transcribe_model=config.transcription.model,
            clear_error=True,
        )
        print(f"Wrote {target.name}", file=sys.stderr)
        transcribed += 1

    print(f"Done. Transcribed {transcribed} file(s) to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
