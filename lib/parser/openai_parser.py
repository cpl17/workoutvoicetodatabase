"""OpenAI-backed parser for workout memo classification and extraction."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from lib.config import Config, ParsingConfig, load_config
from lib.exercises import ExerciseCatalog
from lib.parser.base import ClassifyResult, ParseResult, ParsedSet, ParserBackend

MEMO_TYPES = ("set_log", "exercise_block", "session_recap", "note", "non_workout")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _chat_json(model: str, system: str, user: str) -> dict[str, Any]:
    response = _client().chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI parser returned empty content")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise RuntimeError("OpenAI parser response must be a JSON object")
    return data


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


class OpenAIParser(ParserBackend):
    """Two-step OpenAI parser: classify, then extract structured JSON."""

    def __init__(self, config: ParsingConfig) -> None:
        self.config = config

    def classify(self, transcript: str) -> ClassifyResult:
        system = (
            "You classify gym voice memo transcripts. "
            "Return JSON with keys: is_workout (bool), memo_type (string), "
            "confidence (0-1 float), rationale (string). "
            f"memo_type must be one of: {', '.join(MEMO_TYPES)}. "
            "Use non_workout for unrelated content like driving, testing mics, etc."
        )
        user = f"Transcript:\n{transcript.strip()}"
        data = _chat_json(self.config.model, system, user)

        memo_type = str(data.get("memo_type", "non_workout"))
        if memo_type not in MEMO_TYPES:
            memo_type = "non_workout"

        return ClassifyResult(
            is_workout=bool(data.get("is_workout", False)),
            memo_type=memo_type,
            confidence=float(data.get("confidence", 0.0)),
            rationale=str(data.get("rationale")) if data.get("rationale") else None,
        )

    def extract(
        self,
        transcript: str,
        memo_type: str,
        exercises: ExerciseCatalog,
        *,
        default_weight_unit: str = "lb",
    ) -> ParseResult:
        catalog_context = exercises.context_for_prompt()
        system = (
            "You extract structured strength-training data from gym voice memos. "
            "Correct likely speech-to-text errors using gym context "
            "(e.g. 'person set' may mean 'incline set'). "
            "Return JSON with keys: confidence (0-1 float), sets (array), "
            "context_notes (array of strings), parse_warnings (array of strings). "
            "Each set object may include: exercise_raw, exercise_id, exercise_display, "
            "exercise_confidence, weight, weight_unit, weight_confidence, reps, "
            "reps_confidence, notes. "
            f"Default weight unit is {default_weight_unit}. "
            "Only map exercise_id to active catalog ids when confident; otherwise null."
        )
        user = (
            f"Memo type: {memo_type}\n"
            f"Active exercise catalog:\n{catalog_context}\n\n"
            f"Transcript:\n{transcript.strip()}"
        )
        data = _chat_json(self.config.model, system, user)

        sets: list[ParsedSet] = []
        for raw_set in data.get("sets", []):
            if not isinstance(raw_set, dict):
                continue
            sets.append(
                ParsedSet(
                    exercise_raw=(
                        str(raw_set["exercise_raw"])
                        if raw_set.get("exercise_raw") is not None
                        else None
                    ),
                    exercise_id=(
                        str(raw_set["exercise_id"])
                        if raw_set.get("exercise_id")
                        else None
                    ),
                    exercise_display=(
                        str(raw_set["exercise_display"])
                        if raw_set.get("exercise_display")
                        else None
                    ),
                    exercise_confidence=_coerce_float(raw_set.get("exercise_confidence")),
                    weight=_coerce_float(raw_set.get("weight")),
                    weight_unit=(
                        str(raw_set["weight_unit"])
                        if raw_set.get("weight_unit")
                        else default_weight_unit
                    ),
                    weight_confidence=_coerce_float(raw_set.get("weight_confidence")),
                    reps=_coerce_int(raw_set.get("reps")),
                    reps_confidence=_coerce_float(raw_set.get("reps_confidence")),
                    notes=str(raw_set["notes"]) if raw_set.get("notes") else None,
                )
            )

        context_notes = [
            str(note) for note in data.get("context_notes", []) if isinstance(note, str)
        ]
        parse_warnings = [
            str(warn) for warn in data.get("parse_warnings", []) if isinstance(warn, str)
        ]

        return ParseResult(
            memo_type=memo_type,
            confidence=float(data.get("confidence", 0.0)),
            sets=sets,
            context_notes=context_notes,
            parse_warnings=parse_warnings,
            raw_response=data,
        )


def create_parser_backend(config: Config | None = None) -> ParserBackend:
    """Return the parser backend selected in config.yaml."""
    config = config or load_config()
    if config.parsing.backend == "openai":
        return OpenAIParser(config.parsing)
    raise ValueError(f"Unsupported parsing backend: {config.parsing.backend}")


def parsing_requires_openai_key(config: Config | None = None) -> bool:
    config = config or load_config()
    return config.parsing.backend == "openai"
