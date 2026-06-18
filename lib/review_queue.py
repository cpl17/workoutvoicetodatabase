"""Human-readable review queue stored at data/review_queue.json."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_config


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReviewQueueItem:
    id: str
    memo_id: str
    field: str
    heard: str | None
    proposed: Any
    confidence: float | None
    status: str
    telegram_message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "memo_id": self.memo_id,
            "field": self.field,
            "heard": self.heard,
            "proposed": self.proposed,
            "confidence": self.confidence,
            "status": self.status,
            "telegram_message_id": self.telegram_message_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ReviewQueueItem:
        return cls(
            id=str(raw["id"]),
            memo_id=str(raw["memo_id"]),
            field=str(raw["field"]),
            heard=str(raw["heard"]) if raw.get("heard") is not None else None,
            proposed=raw.get("proposed"),
            confidence=(
                float(raw["confidence"]) if raw.get("confidence") is not None else None
            ),
            status=str(raw.get("status", "pending")),
            telegram_message_id=(
                str(raw["telegram_message_id"])
                if raw.get("telegram_message_id") is not None
                else None
            ),
        )


class ReviewQueue:
    """JSON file queue shared by review.py and future Hermes integration."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_config(cls, config_path: Path | str | None = None) -> ReviewQueue:
        config = load_config(config_path)
        return cls(config.paths.data / "review_queue.json")

    def _load_raw(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"items": []}
        with self.path.open(encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path}: root must be an object")
        if not isinstance(raw.get("items"), list):
            raise ValueError(f"{self.path}: items must be a list")
        return raw

    def _save_raw(self, raw: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(raw, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_items(self, *, status: str | None = "pending") -> list[ReviewQueueItem]:
        raw = self._load_raw()
        items = [ReviewQueueItem.from_dict(item) for item in raw["items"]]
        if status is None:
            return items
        return [item for item in items if item.status == status]

    def get(self, item_id: str) -> ReviewQueueItem | None:
        for item in self.list_items(status=None):
            if item.id == item_id:
                return item
        return None

    def add(
        self,
        *,
        memo_id: str,
        field: str,
        proposed: Any,
        heard: str | None = None,
        confidence: float | None = None,
        item_id: str | None = None,
    ) -> ReviewQueueItem:
        raw = self._load_raw()
        vid = item_id or f"ver-{uuid.uuid4().hex[:8]}"
        item = ReviewQueueItem(
            id=vid,
            memo_id=memo_id,
            field=field,
            heard=heard,
            proposed=proposed,
            confidence=confidence,
            status="pending",
        )
        raw["items"].append(item.to_dict())
        self._save_raw(raw)
        return item

    def update_status(self, item_id: str, status: str) -> ReviewQueueItem | None:
        raw = self._load_raw()
        updated: ReviewQueueItem | None = None
        new_items: list[dict[str, Any]] = []
        for entry in raw["items"]:
            if entry.get("id") == item_id:
                entry["status"] = status
                updated = ReviewQueueItem.from_dict(entry)
            new_items.append(entry)
        if updated is None:
            return None
        raw["items"] = new_items
        self._save_raw(raw)
        return updated

    def sync_from_manifest_items(
        self,
        manifest_items: list[Any],
    ) -> None:
        """Ensure manifest verification items exist in the JSON queue."""
        existing_ids = {item.id for item in self.list_items(status=None)}
        for record in manifest_items:
            if record.id in existing_ids:
                continue
            proposed = json.loads(record.proposed_value)
            heard = None
            if isinstance(proposed, dict):
                heard = proposed.get("heard") or proposed.get("exercise_raw")
            self.add(
                memo_id=record.memo_id,
                field=record.field,
                proposed=proposed,
                heard=str(heard) if heard is not None else None,
                confidence=record.confidence,
                item_id=record.id,
            )
