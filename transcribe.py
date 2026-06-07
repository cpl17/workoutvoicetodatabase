#!/usr/bin/env python3
"""Transcribe an audio file (e.g. MP3) using OpenAI Whisper."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

SUPPORTED_EXTENSIONS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}


def transcribe(audio_path: Path, *, language: str | None = None) -> str:
    client = OpenAI()  # uses OPENAI_API_KEY from environment
    with audio_path.open("rb") as audio_file:
        kwargs = {"model": "whisper-1", "file": audio_file}
        if language:
            kwargs["language"] = language
        result = client.audio.transcriptions.create(**kwargs)
    return result.text


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Transcribe an audio file with Whisper.")
    parser.add_argument("audio", type=Path, help="Path to audio file (mp3, m4a, wav, etc.)")
    parser.add_argument(
        "-l",
        "--language",
        help="ISO-639-1 language code (e.g. en). Optional; Whisper auto-detects if omitted.",
    )
    parser.add_argument("-o", "--output", type=Path, help="Write transcript to this file instead of stdout")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
        return 1

    audio_path = args.audio.expanduser().resolve()
    if not audio_path.is_file():
        print(f"Error: file not found: {audio_path}", file=sys.stderr)
        return 1

    if audio_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(
            f"Warning: extension {audio_path.suffix!r} may not be supported. "
            f"Common types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            file=sys.stderr,
        )

    print(f"Transcribing {audio_path.name}...", file=sys.stderr)
    text = transcribe(audio_path, language=args.language)

    if args.output:
        args.output.write_text(text.strip() + "\n", encoding="utf-8")
        print(f"Wrote transcript to {args.output}", file=sys.stderr)
    else:
        print(text.strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
