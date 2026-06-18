"""SQLite manifest for per-memo pipeline state."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import REPO_ROOT, load_config

EXPORT_STATUSES = frozenset({"pending", "done", "failed"})
TRANSCRIBE_STATUSES = frozenset({"pending", "done", "failed", "skipped"})

MEMOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memos (
    id TEXT PRIMARY KEY,
    apple_recording_path TEXT NOT NULL UNIQUE,
    recorded_at TEXT,
    title TEXT NOT NULL,
    audio_path TEXT,
    transcript_path TEXT,
    export_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (export_status IN ('pending', 'done', 'failed')),
    transcribe_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (transcribe_status IN ('pending', 'done', 'failed', 'skipped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso_timestamp(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _storage_path(path: Path | str | None) -> str | None:
    """Store paths relative to repo root when possible."""
    if path is None:
        return None
    raw = Path(path)
    if not raw.is_absolute():
        return str(raw).replace("\\", "/")
    try:
        return str(raw.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(raw.resolve()).replace("\\", "/")


def _validate_export_status(status: str) -> str:
    if status not in EXPORT_STATUSES:
        allowed = ", ".join(sorted(EXPORT_STATUSES))
        raise ValueError(f"export_status must be one of: {allowed}")
    return status


def _validate_transcribe_status(status: str) -> str:
    if status not in TRANSCRIBE_STATUSES:
        allowed = ", ".join(sorted(TRANSCRIBE_STATUSES))
        raise ValueError(f"transcribe_status must be one of: {allowed}")
    return status


@dataclass(frozen=True)
class MemoRecord:
    id: str
    apple_recording_path: str
    recorded_at: str | None
    title: str
    audio_path: str | None
    transcript_path: str | None
    export_status: str
    transcribe_status: str
    error_message: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> MemoRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            apple_recording_path=row["apple_recording_path"],
            recorded_at=row["recorded_at"],
            title=row["title"],
            audio_path=row["audio_path"],
            transcript_path=row["transcript_path"],
            export_status=row["export_status"],
            transcribe_status=row["transcribe_status"],
            error_message=row["error_message"] if "error_message" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class Manifest:
    """Processing ledger: one row per voice memo, keyed by apple_recording_path."""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        self.db_path = path

    @classmethod
    def from_config(cls, config_path: Path | str | None = None) -> Manifest:
        """Open the manifest at config.paths.data / manifest.db."""
        config = load_config(config_path)
        return cls(config.paths.data / "manifest.db")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        """Create the data directory and memos table if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(MEMOS_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memos)")}
            if "error_message" not in columns:
                conn.execute("ALTER TABLE memos ADD COLUMN error_message TEXT")
            conn.commit()

    def upsert_memo(
        self,
        *,
        apple_recording_path: str,
        title: str,
        recorded_at: datetime | str | None = None,
        audio_path: Path | str | None = None,
        transcript_path: Path | str | None = None,
        export_status: str = "pending",
        transcribe_status: str = "pending",
    ) -> MemoRecord:
        """Insert a new memo or update an existing one matched by apple_recording_path."""
        apple_recording_path = apple_recording_path.strip()
        title = title.strip()
        if not apple_recording_path:
            raise ValueError("apple_recording_path is required")
        if not title:
            raise ValueError("title is required")

        export_status = _validate_export_status(export_status)
        transcribe_status = _validate_transcribe_status(transcribe_status)
        recorded_at_iso = _to_iso_timestamp(recorded_at)
        audio_path_str = _storage_path(audio_path)
        transcript_path_str = _storage_path(transcript_path)
        now = _utc_now_iso()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM memos WHERE apple_recording_path = ?",
                (apple_recording_path,),
            ).fetchone()

            if existing is None:
                memo_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO memos (
                        id, apple_recording_path, recorded_at, title,
                        audio_path, transcript_path,
                        export_status, transcribe_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memo_id,
                        apple_recording_path,
                        recorded_at_iso,
                        title,
                        audio_path_str,
                        transcript_path_str,
                        export_status,
                        transcribe_status,
                        now,
                        now,
                    ),
                )
            else:
                memo_id = existing["id"]
                conn.execute(
                    """
                    UPDATE memos SET
                        recorded_at = ?,
                        title = ?,
                        audio_path = ?,
                        transcript_path = ?,
                        export_status = ?,
                        transcribe_status = ?,
                        updated_at = ?
                    WHERE apple_recording_path = ?
                    """,
                    (
                        recorded_at_iso,
                        title,
                        audio_path_str,
                        transcript_path_str,
                        export_status,
                        transcribe_status,
                        now,
                        apple_recording_path,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memos WHERE id = ?",
                (memo_id,),
            ).fetchone()

        assert row is not None
        return MemoRecord.from_row(row)

    def get_memo_by_apple_path(self, apple_recording_path: str) -> MemoRecord | None:
        """Look up a memo by Apple's ZPATH value."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memos WHERE apple_recording_path = ?",
                (apple_recording_path.strip(),),
            ).fetchone()
        return MemoRecord.from_row(row) if row is not None else None

    def list_memos(
        self,
        *,
        export_status: str | None = None,
        transcribe_status: str | None = None,
    ) -> list[MemoRecord]:
        """Return all memos, optionally filtered by export or transcribe status."""
        query = "SELECT * FROM memos"
        clauses: list[str] = []
        params: list[Any] = []

        if export_status is not None:
            clauses.append("export_status = ?")
            params.append(_validate_export_status(export_status))
        if transcribe_status is not None:
            clauses.append("transcribe_status = ?")
            params.append(_validate_transcribe_status(transcribe_status))

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY recorded_at IS NULL, recorded_at, created_at"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [MemoRecord.from_row(row) for row in rows]

    def update_status(
        self,
        memo_id: str,
        *,
        export_status: str | None = None,
        transcribe_status: str | None = None,
        audio_path: Path | str | None = None,
        transcript_path: Path | str | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> MemoRecord | None:
        """Update pipeline status and/or paths for a memo by id."""
        if (
            export_status is None
            and transcribe_status is None
            and audio_path is None
            and transcript_path is None
            and error_message is None
            and not clear_error
        ):
            raise ValueError("update_status requires at least one field to change")

        fields: list[str] = []
        params: list[Any] = []

        if export_status is not None:
            fields.append("export_status = ?")
            params.append(_validate_export_status(export_status))
        if transcribe_status is not None:
            fields.append("transcribe_status = ?")
            params.append(_validate_transcribe_status(transcribe_status))
        if audio_path is not None:
            fields.append("audio_path = ?")
            params.append(_storage_path(audio_path))
        if transcript_path is not None:
            fields.append("transcript_path = ?")
            params.append(_storage_path(transcript_path))
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        elif clear_error:
            fields.append("error_message = ?")
            params.append(None)

        fields.append("updated_at = ?")
        params.append(_utc_now_iso())
        params.append(memo_id)

        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE memos SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM memos WHERE id = ?",
                (memo_id,),
            ).fetchone()

        return MemoRecord.from_row(row) if row is not None else None
