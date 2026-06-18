"""Pluggable transcription backends for audio → text."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from openai import OpenAI

from lib.config import TranscriptionConfig


@runtime_checkable
class TranscriptionBackend(Protocol):
    """Transcribe a local audio file to plain text."""

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> str: ...


class OpenAIBackend:
    """OpenAI whisper-1 API transcription."""

    def __init__(self, *, model: str = "whisper-1", language: str | None = None) -> None:
        self.model = model
        self.language = language
        self._client = OpenAI()

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        lang = language if language is not None else self.language
        with audio_path.open("rb") as audio_file:
            kwargs: dict[str, object] = {"model": self.model, "file": audio_file}
            if lang:
                kwargs["language"] = lang
            result = self._client.audio.transcriptions.create(**kwargs)
        return result.text


class FasterWhisperBackend:
    """Local faster-whisper transcription; model loads once and reuses across files."""

    def __init__(self, *, model: str = "small", language: str | None = None) -> None:
        from faster_whisper import WhisperModel

        self.model_name = model
        self.language = language
        self._model = WhisperModel(model, device="auto", compute_type="default")

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> str:
        lang = language if language is not None else self.language
        kwargs: dict[str, object] = {}
        if lang:
            kwargs["language"] = lang
        segments, _info = self._model.transcribe(str(audio_path), **kwargs)
        return "".join(segment.text for segment in segments).strip()


def create_transcription_backend(transcription: TranscriptionConfig) -> TranscriptionBackend:
    """Build a transcription backend from config."""
    if transcription.backend == "openai":
        return OpenAIBackend(model=transcription.model, language=transcription.language)
    if transcription.backend == "faster_whisper":
        return FasterWhisperBackend(model=transcription.model, language=transcription.language)
    raise ValueError(f"Unknown transcription backend: {transcription.backend!r}")
