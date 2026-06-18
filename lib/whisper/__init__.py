"""Transcription backends (OpenAI cloud, local faster-whisper)."""

from lib.whisper.backends import FasterWhisperBackend, OpenAIBackend, TranscriptionBackend, create_transcription_backend

__all__ = [
    "FasterWhisperBackend",
    "OpenAIBackend",
    "TranscriptionBackend",
    "create_transcription_backend",
]
