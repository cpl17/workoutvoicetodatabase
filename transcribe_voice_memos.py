#!/usr/bin/env python3
"""Transcribe new audio files from voice-memos/ using OpenAI Whisper."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from transcribe import SUPPORTED_EXTENSIONS, transcribe

DEFAULT_INPUT = Path("voice-memos")
DEFAULT_OUTPUT = Path("transcripts")


def find_audio_files(input_dir: Path) -> list[Path]:
    files = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime)


def transcript_path(audio_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{audio_path.stem}.txt"


def pending_transcriptions(
    input_dir: Path,
    output_dir: Path,
    *,
    force: bool,
) -> list[Path]:
    pending: list[Path] = []
    for audio_path in find_audio_files(input_dir):
        target = transcript_path(audio_path, output_dir)
        if force or not target.exists():
            pending.append(audio_path)
    return pending


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Transcribe new voice memos from a folder with Whisper."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Folder with audio files (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Folder for transcript .txt files (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "-l",
        "--language",
        help="ISO-639-1 language code (e.g. en). Optional; Whisper auto-detects if omitted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transcribe even if a transcript file already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be transcribed without calling Whisper",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
        return 1

    input_dir = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()

    if not input_dir.is_dir():
        print(f"Error: input folder not found: {input_dir}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    pending = pending_transcriptions(input_dir, output_dir, force=args.force)
    if not pending:
        print(f"No new voice memos to transcribe in {input_dir}", file=sys.stderr)
        return 0

    if args.dry_run:
        for audio_path in pending:
            print(audio_path.name)
        return 0

    transcribed = 0
    for audio_path in pending:
        target = transcript_path(audio_path, output_dir)
        print(f"Transcribing {audio_path.name}...", file=sys.stderr)
        text = transcribe(audio_path, language=args.language)
        target.write_text(text.strip() + "\n", encoding="utf-8")
        print(f"Wrote {target.name}", file=sys.stderr)
        transcribed += 1

    print(f"Done. Transcribed {transcribed} file(s) to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
