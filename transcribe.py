#!/usr/bin/env python3
"""Transcribe an audio file using the configured backend (OpenAI or faster-whisper)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from lib.config import Config, load_config
from lib.whisper.backends import TranscriptionBackend, create_transcription_backend

SUPPORTED_EXTENSIONS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}

_backend: TranscriptionBackend | None = None
_backend_key: tuple[str, str, str | None] | None = None


def transcription_requires_openai_key(config: Config | None = None) -> bool:
    """Return True when config selects the OpenAI transcription backend."""
    config = config or load_config()
    return config.transcription.backend == "openai"


def get_transcription_backend(config: Config | None = None) -> TranscriptionBackend:
    """Return a cached backend instance for the current transcription config."""
    global _backend, _backend_key
    config = config or load_config()
    key = (
        config.transcription.backend,
        config.transcription.model,
        config.transcription.language,
    )
    if _backend is None or _backend_key != key:
        _backend = create_transcription_backend(config.transcription)
        _backend_key = key
    return _backend


def transcribe(audio_path: Path, *, language: str | None = None) -> str:
    """Transcribe audio using the backend from config.yaml."""
    config = load_config()
    backend = get_transcription_backend(config)
    lang = language if language is not None else config.transcription.language
    return backend.transcribe(audio_path, language=lang)


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Transcribe an audio file with Whisper.")
    parser.add_argument("audio", type=Path, help="Path to audio file (mp3, m4a, wav, etc.)")
    parser.add_argument(
        "-l",
        "--language",
        help="ISO-639-1 language code (e.g. en). Optional; uses config or auto-detect if omitted.",
    )
    parser.add_argument("-o", "--output", type=Path, help="Write transcript to this file instead of stdout")
    args = parser.parse_args()

    config = load_config()
    if transcription_requires_openai_key(config) and not os.environ.get("OPENAI_API_KEY"):
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

    print(
        f"Transcribing {audio_path.name} ({config.transcription.backend}/{config.transcription.model})...",
        file=sys.stderr,
    )
    text = transcribe(audio_path, language=args.language)

    if args.output:
        args.output.write_text(text.strip() + "\n", encoding="utf-8")
        print(f"Wrote transcript to {args.output}", file=sys.stderr)
    else:
        print(text.strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
