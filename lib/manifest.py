"""SQLite manifest for per-memo pipeline state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import REPO_ROOT, load_config

EXPORT_STATUSES = frozenset({"pending", "done", "failed"})
TRANSCRIBE_STATUSES = frozenset({"pending", "done", "failed", "skipped"})
PARSE_STATUSES = frozenset({"pending", "done", "failed", "skipped", "needs_review"})
VERIFICATION_STATUSES = frozenset({"none", "pending", "approved", "rejected"})
MEMO_TYPES = frozenset(
    {"set_log", "exercise_block", "session_recap", "note", "non_workout"}
)
VERIFICATION_ITEM_STATUSES = frozenset(
    {"pending", "approved", "corrected", "rejected"}
)

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

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    memo_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
"""

VERIFICATION_ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_items (
    id TEXT PRIMARY KEY,
    memo_id TEXT NOT NULL,
    field TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    alternatives TEXT,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'corrected', 'rejected')),
    resolved_value TEXT,
    resolved_at TEXT,
    resolved_via TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (memo_id) REFERENCES memos(id)
);
"""

EXERCISE_ALIASES_SCHEMA = """
CREATE TABLE IF NOT EXISTS exercise_aliases (
    canonical_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (canonical_id, alias)
);
"""

MEMO_PHASE1_COLUMNS: list[tuple[str, str]] = [
    ("parsed_path", "TEXT"),
    ("session_id", "TEXT"),
    ("parse_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("memo_type", "TEXT"),
    ("parse_backend", "TEXT"),
    ("parse_schema_version", "TEXT"),
    ("confidence", "REAL"),
    ("verification_status", "TEXT NOT NULL DEFAULT 'none'"),
]


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


def _validate_parse_status(status: str) -> str:
    if status not in PARSE_STATUSES:
        allowed = ", ".join(sorted(PARSE_STATUSES))
        raise ValueError(f"parse_status must be one of: {allowed}")
    return status


def _validate_verification_status(status: str) -> str:
    if status not in VERIFICATION_STATUSES:
        allowed = ", ".join(sorted(VERIFICATION_STATUSES))
        raise ValueError(f"verification_status must be one of: {allowed}")
    return status


def _validate_memo_type(memo_type: str) -> str:
    if memo_type not in MEMO_TYPES:
        allowed = ", ".join(sorted(MEMO_TYPES))
        raise ValueError(f"memo_type must be one of: {allowed}")
    return memo_type


def _validate_verification_item_status(status: str) -> str:
    if status not in VERIFICATION_ITEM_STATUSES:
        allowed = ", ".join(sorted(VERIFICATION_ITEM_STATUSES))
        raise ValueError(f"verification item status must be one of: {allowed}")
    return status


@dataclass(frozen=True)
class MemoRecord:
    id: str
    apple_recording_path: str
    recorded_at: str | None
    title: str
    audio_path: str | None
    transcript_path: str | None
    parsed_path: str | None
    session_id: str | None
    export_status: str
    transcribe_status: str
    parse_status: str
    memo_type: str | None
    error_message: str | None
    transcribe_backend: str | None
    transcribe_model: str | None
    parse_backend: str | None
    parse_schema_version: str | None
    confidence: float | None
    verification_status: str
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
            parsed_path=row["parsed_path"] if "parsed_path" in keys else None,
            session_id=row["session_id"] if "session_id" in keys else None,
            export_status=row["export_status"],
            transcribe_status=row["transcribe_status"],
            parse_status=row["parse_status"] if "parse_status" in keys else "pending",
            memo_type=row["memo_type"] if "memo_type" in keys else None,
            error_message=row["error_message"] if "error_message" in keys else None,
            transcribe_backend=row["transcribe_backend"] if "transcribe_backend" in keys else None,
            transcribe_model=row["transcribe_model"] if "transcribe_model" in keys else None,
            parse_backend=row["parse_backend"] if "parse_backend" in keys else None,
            parse_schema_version=(
                row["parse_schema_version"] if "parse_schema_version" in keys else None
            ),
            confidence=row["confidence"] if "confidence" in keys else None,
            verification_status=(
                row["verification_status"] if "verification_status" in keys else "none"
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class SessionRecord:
    id: str
    started_at: str
    ended_at: str
    memo_count: int
    notes: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SessionRecord:
        return cls(
            id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            memo_count=row["memo_count"],
            notes=row["notes"],
        )


@dataclass(frozen=True)
class VerificationItemRecord:
    id: str
    memo_id: str
    field: str
    proposed_value: str
    alternatives: str | None
    confidence: float | None
    status: str
    resolved_value: str | None
    resolved_at: str | None
    resolved_via: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VerificationItemRecord:
        return cls(
            id=row["id"],
            memo_id=row["memo_id"],
            field=row["field"],
            proposed_value=row["proposed_value"],
            alternatives=row["alternatives"],
            confidence=row["confidence"],
            status=row["status"],
            resolved_value=row["resolved_value"],
            resolved_at=row["resolved_at"],
            resolved_via=row["resolved_via"],
            created_at=row["created_at"],
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
        """Create tables and apply Phase 1 column migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(MEMOS_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memos)")}
            legacy_columns = {
                "error_message": "TEXT",
                "transcribe_backend": "TEXT",
                "transcribe_model": "TEXT",
            }
            for name, col_type in legacy_columns.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE memos ADD COLUMN {name} {col_type}")
            for name, col_type in MEMO_PHASE1_COLUMNS:
                if name not in columns:
                    conn.execute(f"ALTER TABLE memos ADD COLUMN {name} {col_type}")
            conn.executescript(SESSIONS_SCHEMA)
            conn.executescript(VERIFICATION_ITEMS_SCHEMA)
            conn.executescript(EXERCISE_ALIASES_SCHEMA)
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

    def get_memo(self, memo_id: str) -> MemoRecord | None:
        """Look up a memo by id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memos WHERE id = ?",
                (memo_id,),
            ).fetchone()
        return MemoRecord.from_row(row) if row is not None else None

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
        parse_status: str | None = None,
    ) -> list[MemoRecord]:
        """Return all memos, optionally filtered by pipeline status."""
        query = "SELECT * FROM memos"
        clauses: list[str] = []
        params: list[Any] = []

        if export_status is not None:
            clauses.append("export_status = ?")
            params.append(_validate_export_status(export_status))
        if transcribe_status is not None:
            clauses.append("transcribe_status = ?")
            params.append(_validate_transcribe_status(transcribe_status))
        if parse_status is not None:
            clauses.append("parse_status = ?")
            params.append(_validate_parse_status(parse_status))

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
        parse_status: str | None = None,
        audio_path: Path | str | None = None,
        transcript_path: Path | str | None = None,
        parsed_path: Path | str | None = None,
        session_id: str | None = None,
        memo_type: str | None = None,
        error_message: str | None = None,
        transcribe_backend: str | None = None,
        transcribe_model: str | None = None,
        parse_backend: str | None = None,
        parse_schema_version: str | None = None,
        confidence: float | None = None,
        verification_status: str | None = None,
        clear_error: bool = False,
    ) -> MemoRecord | None:
        """Update pipeline status and/or paths for a memo by id."""
        if (
            export_status is None
            and transcribe_status is None
            and parse_status is None
            and audio_path is None
            and transcript_path is None
            and parsed_path is None
            and session_id is None
            and memo_type is None
            and error_message is None
            and transcribe_backend is None
            and transcribe_model is None
            and parse_backend is None
            and parse_schema_version is None
            and confidence is None
            and verification_status is None
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
        if parse_status is not None:
            fields.append("parse_status = ?")
            params.append(_validate_parse_status(parse_status))
        if audio_path is not None:
            fields.append("audio_path = ?")
            params.append(_storage_path(audio_path))
        if transcript_path is not None:
            fields.append("transcript_path = ?")
            params.append(_storage_path(transcript_path))
        if parsed_path is not None:
            fields.append("parsed_path = ?")
            params.append(_storage_path(parsed_path))
        if session_id is not None:
            fields.append("session_id = ?")
            params.append(session_id)
        if memo_type is not None:
            fields.append("memo_type = ?")
            params.append(_validate_memo_type(memo_type))
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        elif clear_error:
            fields.append("error_message = ?")
            params.append(None)
        if transcribe_backend is not None:
            fields.append("transcribe_backend = ?")
            params.append(transcribe_backend)
        if transcribe_model is not None:
            fields.append("transcribe_model = ?")
            params.append(transcribe_model)
        if parse_backend is not None:
            fields.append("parse_backend = ?")
            params.append(parse_backend)
        if parse_schema_version is not None:
            fields.append("parse_schema_version = ?")
            params.append(parse_schema_version)
        if confidence is not None:
            fields.append("confidence = ?")
            params.append(confidence)
        if verification_status is not None:
            fields.append("verification_status = ?")
            params.append(_validate_verification_status(verification_status))

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

    def upsert_session(
        self,
        *,
        session_id: str,
        started_at: str,
        ended_at: str,
        memo_count: int,
        notes: str | None = None,
    ) -> SessionRecord:
        """Insert or replace a workout session row."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, started_at, ended_at, memo_count, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    memo_count = excluded.memo_count,
                    notes = excluded.notes
                """,
                (session_id, started_at, ended_at, memo_count, notes),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row is not None
        return SessionRecord.from_row(row)

    def list_sessions(self) -> list[SessionRecord]:
        """Return all sessions ordered by start time."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at"
            ).fetchall()
        return [SessionRecord.from_row(row) for row in rows]

    def add_verification_item(
        self,
        *,
        memo_id: str,
        field: str,
        proposed_value: Any,
        alternatives: list[Any] | None = None,
        confidence: float | None = None,
        item_id: str | None = None,
    ) -> VerificationItemRecord:
        """Create a verification item for human review."""
        now = _utc_now_iso()
        vid = item_id or f"ver-{uuid.uuid4().hex[:8]}"
        proposed_json = json.dumps(proposed_value)
        alternatives_json = json.dumps(alternatives) if alternatives else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verification_items (
                    id, memo_id, field, proposed_value, alternatives,
                    confidence, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (vid, memo_id, field, proposed_json, alternatives_json, confidence, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM verification_items WHERE id = ?",
                (vid,),
            ).fetchone()
        assert row is not None
        return VerificationItemRecord.from_row(row)

    def list_verification_items(
        self,
        *,
        status: str | None = "pending",
        memo_id: str | None = None,
    ) -> list[VerificationItemRecord]:
        """Return verification items, optionally filtered."""
        query = "SELECT * FROM verification_items"
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(_validate_verification_item_status(status))
        if memo_id is not None:
            clauses.append("memo_id = ?")
            params.append(memo_id)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [VerificationItemRecord.from_row(row) for row in rows]

    def resolve_verification_item(
        self,
        item_id: str,
        *,
        status: str,
        resolved_value: Any | None = None,
        resolved_via: str = "cli",
    ) -> VerificationItemRecord | None:
        """Mark a verification item as approved, corrected, or rejected."""
        status = _validate_verification_item_status(status)
        resolved_json = json.dumps(resolved_value) if resolved_value is not None else None
        now = _utc_now_iso()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE verification_items SET
                    status = ?,
                    resolved_value = ?,
                    resolved_at = ?,
                    resolved_via = ?
                WHERE id = ?
                """,
                (status, resolved_json, now, resolved_via, item_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM verification_items WHERE id = ?",
                (item_id,),
            ).fetchone()

        return VerificationItemRecord.from_row(row) if row is not None else None

    def upsert_exercise_alias(
        self,
        *,
        canonical_id: str,
        alias: str,
        source: str,
    ) -> None:
        """Record a spoken alias for a canonical exercise id."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exercise_aliases (canonical_id, alias, source)
                VALUES (?, ?, ?)
                ON CONFLICT(canonical_id, alias) DO UPDATE SET source = excluded.source
                """,
                (canonical_id, alias.strip().lower(), source),
            )
            conn.commit()
