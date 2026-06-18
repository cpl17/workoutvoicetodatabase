"""Exercise catalog: load, save, and lookup from exercises.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lib.config import REPO_ROOT

EXERCISE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_EXERCISES_PATH = REPO_ROOT / "exercises.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(name: str) -> str:
    """Convert a display name to a canonical exercise id."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    slug = slug.strip("_")
    if not slug or not EXERCISE_ID_PATTERN.match(slug):
        raise ValueError(f"Cannot derive exercise id from {name!r}")
    return slug


@dataclass(frozen=True)
class Exercise:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    status: str
    verified_at: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "status": self.status,
        }
        if self.verified_at is not None:
            data["verified_at"] = self.verified_at
        if self.source is not None:
            data["source"] = self.source
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Exercise:
        aliases = raw.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            raise ValueError("exercise.aliases must be a non-empty list")
        return cls(
            id=str(raw["id"]),
            display_name=str(raw["display_name"]),
            aliases=tuple(str(a) for a in aliases),
            status=str(raw["status"]),
            verified_at=str(raw["verified_at"]) if raw.get("verified_at") else None,
            source=str(raw["source"]) if raw.get("source") else None,
        )


class ExerciseCatalog:
    """Canonical exercise list with candidate/active/rejected lifecycle."""

    def __init__(self, exercises: list[Exercise], *, schema_version: str = "v1") -> None:
        self.schema_version = schema_version
        self._exercises = list(exercises)

    @classmethod
    def load(cls, path: Path | str | None = None) -> ExerciseCatalog:
        catalog_path = Path(path) if path else DEFAULT_EXERCISES_PATH
        if not catalog_path.is_file():
            return cls([])
        with catalog_path.open(encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"{catalog_path}: root must be an object")
        exercises_raw = raw.get("exercises")
        if not isinstance(exercises_raw, list):
            raise ValueError(f"{catalog_path}: exercises must be a list")
        exercises = [Exercise.from_dict(item) for item in exercises_raw]
        return cls(exercises, schema_version=str(raw.get("schema_version", "v1")))

    def save(self, path: Path | str | None = None) -> None:
        catalog_path = Path(path) if path else DEFAULT_EXERCISES_PATH
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "exercises": [ex.to_dict() for ex in self._exercises],
        }
        catalog_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def all(self) -> list[Exercise]:
        return list(self._exercises)

    def active(self) -> list[Exercise]:
        return [ex for ex in self._exercises if ex.status == "active"]

    def candidates(self) -> list[Exercise]:
        return [ex for ex in self._exercises if ex.status == "candidate"]

    def get(self, exercise_id: str) -> Exercise | None:
        for ex in self._exercises:
            if ex.id == exercise_id:
                return ex
        return None

    def find_by_alias(self, alias: str) -> Exercise | None:
        needle = alias.strip().lower()
        for ex in self._exercises:
            if ex.status != "active":
                continue
            for candidate in ex.aliases:
                if candidate.strip().lower() == needle:
                    return ex
        return None

    def add_candidate(
        self,
        *,
        display_name: str,
        aliases: list[str],
        source: str = "bootstrap",
        exercise_id: str | None = None,
    ) -> Exercise:
        """Add a candidate exercise if not already present."""
        ex_id = exercise_id or slugify(display_name)
        existing = self.get(ex_id)
        if existing is not None:
            return existing

        normalized_aliases = tuple(
            dict.fromkeys(a.strip() for a in aliases if a.strip())
        )
        if not normalized_aliases:
            normalized_aliases = (display_name.strip(),)

        exercise = Exercise(
            id=ex_id,
            display_name=display_name.strip(),
            aliases=normalized_aliases,
            status="candidate",
            source=source,
        )
        self._exercises.append(exercise)
        return exercise

    def approve(self, exercise_id: str, *, extra_aliases: list[str] | None = None) -> Exercise:
        """Promote a candidate to active."""
        exercise = self.get(exercise_id)
        if exercise is None:
            raise KeyError(f"exercise not found: {exercise_id}")

        aliases = list(exercise.aliases)
        if extra_aliases:
            for alias in extra_aliases:
                alias = alias.strip()
                if alias and alias not in aliases:
                    aliases.append(alias)

        approved = Exercise(
            id=exercise.id,
            display_name=exercise.display_name,
            aliases=tuple(aliases),
            status="active",
            verified_at=_utc_now_iso(),
            source=exercise.source,
        )
        self._exercises = [
            approved if ex.id == exercise_id else ex for ex in self._exercises
        ]
        return approved

    def reject(self, exercise_id: str) -> Exercise:
        exercise = self.get(exercise_id)
        if exercise is None:
            raise KeyError(f"exercise not found: {exercise_id}")
        rejected = Exercise(
            id=exercise.id,
            display_name=exercise.display_name,
            aliases=exercise.aliases,
            status="rejected",
            verified_at=_utc_now_iso(),
            source=exercise.source,
        )
        self._exercises = [
            rejected if ex.id == exercise_id else ex for ex in self._exercises
        ]
        return rejected

    def context_for_prompt(self) -> str:
        """Serialize active exercises for LLM prompts."""
        active = self.active()
        if not active:
            return "No active exercises in catalog yet."
        lines = []
        for ex in active:
            alias_text = ", ".join(ex.aliases)
            lines.append(f"- {ex.id}: {ex.display_name} (aliases: {alias_text})")
        return "\n".join(lines)
