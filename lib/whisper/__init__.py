"""Transcription backends (OpenAI cloud, local faster-whisper)."""

from lib.whisper.backends import FasterWhisperBackend, OpenAIBackend, TranscriptionBackend

__all__ = ["FasterWhisperBackend", "OpenAIBackend", "TranscriptionBackend"]
