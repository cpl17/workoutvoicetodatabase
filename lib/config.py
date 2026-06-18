"""Load and validate config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

TRANSCRIPTION_BACKENDS = frozenset({"openai", "faster_whisper"})
PARSING_BACKENDS = frozenset({"openai", "local"})


@dataclass(frozen=True)
class PathsConfig:
    voice_memos: Path
    transcripts: Path
    data: Path


@dataclass(frozen=True)
class TranscriptionConfig:
    backend: str
    model: str
    language: str | None


@dataclass(frozen=True)
class ParsingConfig:
    backend: str
    model: str
    schema_version: str
    confidence_threshold: float


@dataclass(frozen=True)
class SessionsConfig:
    window_minutes: int
    timezone: str


@dataclass(frozen=True)
class UnitsConfig:
    default_weight: str


@dataclass(frozen=True)
class VerificationConfig:
    require_for: tuple[str, ...]


@dataclass(frozen=True)
class NonWorkoutConfig:
    action: str


@dataclass(frozen=True)
class Config:
    paths: PathsConfig
    transcription: TranscriptionConfig
    parsing: ParsingConfig
    sessions: SessionsConfig
    units: UnitsConfig
    verification: VerificationConfig
    non_workout: NonWorkoutConfig
    config_path: Path


def _require_mapping(data: Any, key: str) -> dict[str, Any]:
    section = data.get(key)
    if not isinstance(section, dict):
        raise ValueError(f"config.yaml: '{key}' must be a mapping")
    return section


def _require_str(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"config.yaml: '{key}' must be a non-empty string")
    return value.strip()


def _optional_str(section: dict[str, Any], key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"config.yaml: '{key}' must be a string or null")
    value = value.strip()
    return value or None


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def load_config(path: Path | str | None = None) -> Config:
    """Load config.yaml from path (default: repo-root/config.yaml)."""
    config_path = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")

    repo_root = config_path.parent
    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config.yaml must be a mapping at top level: {config_path}")

    paths_raw = _require_mapping(raw, "paths")
    transcription_raw = _require_mapping(raw, "transcription")
    parsing_raw = _require_mapping(raw, "parsing")

    paths = PathsConfig(
        voice_memos=_resolve_path(repo_root, _require_str(paths_raw, "voice_memos")),
        transcripts=_resolve_path(repo_root, _require_str(paths_raw, "transcripts")),
        data=_resolve_path(repo_root, _require_str(paths_raw, "data")),
    )

    transcription_backend = _require_str(transcription_raw, "backend")
    if transcription_backend not in TRANSCRIPTION_BACKENDS:
        allowed = ", ".join(sorted(TRANSCRIPTION_BACKENDS))
        raise ValueError(
            f"config.yaml: transcription.backend must be one of: {allowed}"
        )

    parsing_backend = _require_str(parsing_raw, "backend")
    if parsing_backend not in PARSING_BACKENDS:
        allowed = ", ".join(sorted(PARSING_BACKENDS))
        raise ValueError(f"config.yaml: parsing.backend must be one of: {allowed}")

    confidence = parsing_raw.get("confidence_threshold")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("config.yaml: parsing.confidence_threshold must be between 0 and 1")

    sessions_raw = _require_mapping(raw, "sessions")
    window_minutes = sessions_raw.get("window_minutes")
    if not isinstance(window_minutes, int) or window_minutes <= 0:
        raise ValueError("config.yaml: sessions.window_minutes must be a positive integer")

    units_raw = _require_mapping(raw, "units")
    verification_raw = _require_mapping(raw, "verification")
    non_workout_raw = _require_mapping(raw, "non_workout")

    require_for = verification_raw.get("require_for")
    if not isinstance(require_for, list) or not require_for:
        raise ValueError("config.yaml: verification.require_for must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in require_for):
        raise ValueError("config.yaml: verification.require_for entries must be non-empty strings")

    non_workout_action = _require_str(non_workout_raw, "action")
    if non_workout_action not in {"ignore"}:
        raise ValueError("config.yaml: non_workout.action must be 'ignore'")

    return Config(
        paths=paths,
        transcription=TranscriptionConfig(
            backend=transcription_backend,
            model=_require_str(transcription_raw, "model"),
            language=_optional_str(transcription_raw, "language"),
        ),
        parsing=ParsingConfig(
            backend=parsing_backend,
            model=_require_str(parsing_raw, "model"),
            schema_version=_require_str(parsing_raw, "schema_version"),
            confidence_threshold=float(confidence),
        ),
        sessions=SessionsConfig(
            window_minutes=window_minutes,
            timezone=_require_str(sessions_raw, "timezone"),
        ),
        units=UnitsConfig(default_weight=_require_str(units_raw, "default_weight")),
        verification=VerificationConfig(require_for=tuple(item.strip() for item in require_for)),
        non_workout=NonWorkoutConfig(action=non_workout_action),
        config_path=config_path,
    )
