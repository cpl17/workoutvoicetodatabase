"""Pluggable parser backends for workout memo classification and extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from lib.exercises import ExerciseCatalog


@dataclass(frozen=True)
class ClassifyResult:
    is_workout: bool
    memo_type: str
    confidence: float
    rationale: str | None = None


@dataclass(frozen=True)
class ParsedSet:
    exercise_raw: str | None
    exercise_id: str | None
    exercise_display: str | None
    exercise_confidence: float | None
    weight: float | None
    weight_unit: str | None
    weight_confidence: float | None
    reps: int | None
    reps_confidence: float | None
    notes: str | None = None


@dataclass
class ParseResult:
    memo_type: str
    confidence: float
    sets: list[ParsedSet] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None


class ParserBackend(ABC):
    """Classify transcripts and extract structured workout data."""

    @abstractmethod
    def classify(self, transcript: str) -> ClassifyResult:
        """Determine whether a transcript is workout-related and its memo type."""

    @abstractmethod
    def extract(
        self,
        transcript: str,
        memo_type: str,
        exercises: ExerciseCatalog,
        *,
        default_weight_unit: str = "lb",
    ) -> ParseResult:
        """Extract structured sets and notes from a workout transcript."""
