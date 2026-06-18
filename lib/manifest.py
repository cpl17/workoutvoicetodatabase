"""SQLite manifest for per-memo pipeline state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
            conn.commit()
