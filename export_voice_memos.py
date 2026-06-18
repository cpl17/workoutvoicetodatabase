#!/usr/bin/env python3
"""Export Voice Memos to a normal folder.

Reads Apple's CloudRecordings.db and copies synced .m4a files into voice-memos/.
Requires Full Disk Access for Terminal/Cursor.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lib.manifest import Manifest

DEFAULT_OUTPUT = Path("voice-memos")
RECORDINGS_DIR = (
    Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)
DB_PATH = RECORDINGS_DIR / "CloudRecordings.db"
# Apple stores Core Data timestamps as seconds since 2001-01-01 UTC, not Unix epoch.
APPLE_EPOCH_OFFSET = 978307200


def safe_filename(text: str) -> str:
    """Make a string safe for use as a filename (strip illegal chars, collapse whitespace)."""
    cleaned = re.sub(r'[\\/:|*?"<>]', "-", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Voice Memo"


def unique_path(directory: Path, filename: str) -> Path:
    """Return directory/filename, or directory/stem (2).ext, (3).ext, ... if taken."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 2
    while True:
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def apple_date_to_datetime(raw: str | None) -> datetime | None:
    """Convert an Apple Core Data timestamp string to a UTC datetime."""
    if not raw:
        return None
    try:
        seconds = float(raw) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def export_filename(title: str, recorded_at: datetime | None) -> str:
    """Build output filename: 'YYYY-MM-DD HH.MM.SS Title.m4a'."""
    stem = safe_filename(title)
    if recorded_at:
        stamp = recorded_at.astimezone().strftime("%Y-%m-%d %H.%M.%S")
        stem = f"{stamp} {stem}"
    return f"{stem}.m4a"


def check_library_access() -> None:
    """Verify the Voice Memos DB exists and is readable (needs Full Disk Access)."""
    if not DB_PATH.is_file():
        raise RuntimeError(
            f"Voice Memos database not found at:\n  {DB_PATH}\n"
            "Open Voice Memos on your Mac, wait for iCloud sync, then retry."
        )
    try:
        DB_PATH.read_bytes()[:1]
    except PermissionError as exc:
        raise RuntimeError(
            "Full Disk Access required to read Voice Memos files.\n"
            "System Settings → Privacy & Security → Full Disk Access → enable "
            "Terminal (or Cursor), then quit and reopen it."
        ) from exc


def list_from_library() -> list[dict[str, object]]:
    """List all memos from Apple's CloudRecordings.db.

    Returns dicts with keys: apple_recording_path (ZPATH), title, recorded_at,
    source (Path to .m4a). Ordered oldest-first by ZDATE.
    """
    check_library_access()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT ZPATH, ZENCRYPTEDTITLE, ZDATE
            FROM ZCLOUDRECORDING
            WHERE ZPATH IS NOT NULL AND ZPATH != ''
            ORDER BY ZDATE
            """
        ).fetchall()
    finally:
        conn.close()

    memos: list[dict[str, object]] = []
    for path, title, date_raw in rows:
        title_text = (title or "").strip() or Path(str(path)).stem
        recorded_at = apple_date_to_datetime(str(date_raw) if date_raw is not None else None)
        source = RECORDINGS_DIR / str(path)
        memos.append(
            {
                "apple_recording_path": str(path),
                "title": title_text,
                "recorded_at": recorded_at,
                "source": source,
            }
        )
    return memos


@dataclass(frozen=True)
class ExportResult:
    """Result of attempting to export one memo to voice-memos/."""

    audio_path: Path | None
    copied: bool

def sync_export_to_manifest(
    manifest: Manifest,
    memo: dict[str, object],
    audio_path: Path,
) -> None:
    """Record a successful export in the manifest, preserving existing transcribe state."""
    apple_recording_path = str(memo["apple_recording_path"])
    title = str(memo["title"])
    recorded_at = memo.get("recorded_at")

    existing = manifest.get_memo_by_apple_path(apple_recording_path)
    if existing:
        manifest.update_status(
            existing.id,
            export_status="done",
            audio_path=audio_path,
        )
        return

    manifest.upsert_memo(
        apple_recording_path=apple_recording_path,
        title=title,
        recorded_at=recorded_at if isinstance(recorded_at, datetime) else None,
        audio_path=audio_path,
        export_status="done",
        transcribe_status="pending",
    )


def export_from_library(
    memo: dict[str, object],
    output_dir: Path,
    *,
    force: bool,
) -> ExportResult:
    """Copy one memo's .m4a from the Apple library folder into output_dir.

    Skips copying if the target file already exists (unless force=True).
    Returns ExportResult with the on-disk path (even when skipped) and whether
    a new copy was written.
    """
    source = memo["source"]
    assert isinstance(source, Path)
    title = str(memo["title"])
    recorded_at = memo.get("recorded_at")
    assert recorded_at is None or isinstance(recorded_at, datetime)

    filename = export_filename(title, recorded_at)
    target = output_dir / filename
    if target.exists() and not force:
        print(f"skip  {target.name}", file=sys.stderr)
        return ExportResult(audio_path=target, copied=False)

    if not source.is_file():
        print(f"warn  missing source for {title!r}: {source.name}", file=sys.stderr)
        return ExportResult(audio_path=None, copied=False)

    try:
        source.read_bytes()[:1]
    except PermissionError as exc:
        raise RuntimeError(
            "Full Disk Access required to copy Voice Memos audio files."
        ) from exc

    target = unique_path(output_dir, filename) if not target.exists() else target
    shutil.copy2(source, target)
    if recorded_at:
        ts = recorded_at.timestamp()
        os.utime(target, (ts, ts))

    print(f"saved {target.name}", file=sys.stderr)
    return ExportResult(audio_path=target, copied=True)


def main() -> int:
    """CLI entry point: list or export memos from the Apple Voice Memos library."""
    parser = argparse.ArgumentParser(
        description="Export Voice Memos to a normal folder (reads CloudRecordings.db)."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output folder (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Export at most this many memos",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export even if the output file already exists",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List memos and exit",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not read or write data/manifest.db",
    )
    args = parser.parse_args()

    output_dir = args.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        memos = list_from_library()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        for memo in memos:
            title = str(memo["title"])
            recorded_at = memo.get("recorded_at")
            if isinstance(recorded_at, datetime):
                print(f"{title}  ({recorded_at.astimezone().strftime('%Y-%m-%d %H:%M')})")
            else:
                print(title)
        return 0

    to_export = memos[: args.limit] if args.limit else memos
    print(f"Exporting {len(to_export)} memo(s) to {output_dir}...", file=sys.stderr)

    manifest: Manifest | None = None
    if not args.no_manifest:
        manifest = Manifest.from_config()
        manifest.init_db()

    exported = 0
    for memo in to_export:
        try:
            result = export_from_library(memo, output_dir, force=args.force)
            if result.audio_path and manifest is not None:
                sync_export_to_manifest(manifest, memo, result.audio_path)
            if result.copied:
                exported += 1
        except RuntimeError as exc:
            print(f"Error exporting {memo['title']!r}: {exc}", file=sys.stderr)
            return 1
    print(f"Done. Exported {exported} file(s) to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
