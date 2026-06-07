#!/usr/bin/env python3
"""Export Voice Memos to a normal folder.

Two methods:

  library (default)
    Reads Apple's CloudRecordings.db and copies .m4a files.
    Requires Full Disk Access for Terminal/Cursor.

  ui
    Automates the Voice Memos app via AppleScript (Cmd+C → clipboard).
    Requires Accessibility permission. Fragile across macOS versions.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT = Path("voice-memos")
RECORDINGS_DIR = (
    Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)
DB_PATH = RECORDINGS_DIR / "CloudRecordings.db"
APPLE_EPOCH_OFFSET = 978307200
SIDEBAR_DEPTH_MIN = 5
SIDEBAR_DEPTH_MAX = 20
SKIP_UI_NAMES = {"All Recordings", "Apple Watch", "Recently Deleted", "Voice Memos"}


def run_applescript(script: str, *, timeout: float = 120) -> str:
    proc = subprocess.run(
        ["osascript", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or "AppleScript failed"
        if "-1719" in err or "assistive access" in err.lower():
            raise RuntimeError(
                "Accessibility permission required. System Settings → Privacy & Security "
                "→ Accessibility → enable Terminal (or Cursor), then quit and reopen it."
            )
        raise RuntimeError(err)
    return proc.stdout.strip()


def safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:|*?"<>]', "-", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Voice Memo"


def unique_path(directory: Path, filename: str) -> Path:
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
    if not raw:
        return None
    try:
        seconds = float(raw) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def export_filename(title: str, recorded_at: datetime | None) -> str:
    stem = safe_filename(title)
    if recorded_at:
        stamp = recorded_at.astimezone().strftime("%Y-%m-%d %H.%M.%S")
        stem = f"{stamp} {stem}"
    return f"{stem}.m4a"


def check_library_access() -> None:
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
                "title": title_text,
                "recorded_at": recorded_at,
                "source": source,
            }
        )
    return memos


def export_from_library(
    memo: dict[str, object],
    output_dir: Path,
    *,
    force: bool,
) -> Path | None:
    source = memo["source"]
    assert isinstance(source, Path)
    title = str(memo["title"])
    recorded_at = memo.get("recorded_at")
    assert recorded_at is None or isinstance(recorded_at, datetime)

    filename = export_filename(title, recorded_at)
    target = output_dir / filename
    if target.exists() and not force:
        print(f"skip  {target.name}", file=sys.stderr)
        return None

    if not source.is_file():
        print(f"warn  missing source for {title!r}: {source.name}", file=sys.stderr)
        return None

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
        import os

        os.utime(target, (ts, ts))

    print(f"saved {target.name}", file=sys.stderr)
    return target


def sidebar_group_ref(depth: int) -> str:
    group_ref = "window 1"
    for _ in range(depth):
        group_ref = f"group 1 of {group_ref}"
    return group_ref


def list_from_ui(*, depth: int, ui_delay: float) -> list[dict[str, str]]:
    group_ref = sidebar_group_ref(depth)
    script = f"""
tell application "VoiceMemos" to activate
delay {ui_delay}
tell application "System Events"
    tell process "VoiceMemos"
        set theGroup to {group_ref}
        repeat 20 times
            perform action "AXScrollUpByPage" of theGroup
        end repeat
        delay {ui_delay}

        set btnCount to count of buttons of theGroup
        set results to {{}}
        repeat with i from 1 to btnCount
            set btnRef to button i of theGroup
            set btnName to ""
            set btnDate to ""
            try
                set btnName to value of text field 1 of group 1 of btnRef
            end try
            try
                set btnDesc to description of btnRef
                if btnDesc contains ", " then
                    set AppleScript's text item delimiters to ", "
                    set descParts to text items of btnDesc
                    set AppleScript's text item delimiters to ""
                    if (count of descParts) > 1 then
                        set btnDate to item 2 of descParts
                    end if
                end if
            end try
            if btnName is not "" then
                copy (i as text) & tab & btnName & tab & btnDate to end of results
            end if
        end repeat
        set AppleScript's text item delimiters to linefeed
        return results as text
    end tell
end tell
"""
    memos: list[dict[str, str]] = []
    output = run_applescript(script)
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        if name in SKIP_UI_NAMES:
            continue
        memos.append(
            {
                "position": parts[0].strip(),
                "name": name,
                "date": parts[2].strip() if len(parts) > 2 else "",
            }
        )
    return memos


def list_from_ui_dynamic(*, ui_delay: float) -> list[dict[str, str]]:
    skip_list = ", ".join(f'"{name}"' for name in sorted(SKIP_UI_NAMES))
    script = f"""
tell application "VoiceMemos" to activate
delay {ui_delay}
tell application "System Events"
    tell process "VoiceMemos"
        set skipNames to {{{skip_list}}}
        set found to {{}}
        repeat with e in (entire contents of window 1)
            try
                set elemName to ""
                if class of e is button then
                    try
                        set elemName to value of text field 1 of group 1 of e
                    end try
                else if class of e is row then
                    try
                        set elemName to value of static text 1 of e
                    end try
                    try
                        if elemName is "" then
                            set elemName to value of text field 1 of e
                        end if
                    end try
                end if
                if elemName is not "" and elemName is not in skipNames then
                    if found does not contain elemName then
                        set end of found to elemName
                    end if
                end if
            end try
        end repeat
        set AppleScript's text item delimiters to linefeed
        return found as text
    end tell
end tell
"""
    memos: list[dict[str, str]] = []
    for name in run_applescript(script).splitlines():
        name = name.strip()
        if name:
            memos.append({"name": name, "date": ""})
    return memos


def detect_ui_depth(*, ui_delay: float) -> int:
    for depth in range(SIDEBAR_DEPTH_MIN, SIDEBAR_DEPTH_MAX + 1):
        try:
            memos = list_from_ui(depth=depth, ui_delay=ui_delay)
        except RuntimeError:
            continue
        if memos:
            return depth
    raise RuntimeError("fixed-depth UI scan found no recordings")


def select_and_copy_ui(name: str, *, depth: int | None, ui_delay: float) -> None:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    if depth is not None:
        group_ref = sidebar_group_ref(depth)
        locate_block = f"""
        set theGroup to {group_ref}
        set btnCount to count of buttons of theGroup
        repeat with i from 1 to btnCount
            set btnRef to button i of theGroup
            set btnName to ""
            try
                set btnName to value of text field 1 of group 1 of btnRef
            end try
            if btnName is "{escaped}" then
                perform action "AXPress" of btnRef
                delay {ui_delay * 2}
                keystroke "c" using command down
                delay {ui_delay}
                return "ok"
            end if
        end repeat
"""
    else:
        locate_block = f"""
        repeat with e in (entire contents of window 1)
            try
                set elemName to ""
                if class of e is button then
                    try
                        set elemName to value of text field 1 of group 1 of e
                    end try
                else if class of e is row then
                    try
                        set elemName to value of static text 1 of e
                    end try
                    try
                        if elemName is "" then
                            set elemName to value of text field 1 of e
                        end if
                    end try
                end if
                if elemName is "{escaped}" then
                    perform action "AXPress" of e
                    delay {ui_delay * 2}
                    keystroke "c" using command down
                    delay {ui_delay}
                    return "ok"
                end if
            end try
        end repeat
"""
    script = f"""
tell application "VoiceMemos" to activate
delay {ui_delay}
tell application "System Events"
    tell process "VoiceMemos"
{locate_block}
    end tell
end tell
return "not-found"
"""
    if run_applescript(script) != "ok":
        raise RuntimeError(f'Recording not found in Voice Memos: "{name}"')


def save_clipboard_audio(target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(target_path).replace('"', '\\"')
    script = f"""
set theData to the clipboard as «class M4A »
set thePath to POSIX file "{escaped}"
set theFile to open for access thePath with write permission
write theData to theFile
close access theFile
return "ok"
"""
    run_applescript(script)


def export_from_ui(
    memo: dict[str, str],
    output_dir: Path,
    *,
    depth: int | None,
    ui_delay: float,
    force: bool,
) -> Path | None:
    stem = safe_filename(memo["name"])
    if memo.get("date"):
        stem = f"{stem} - {safe_filename(memo['date'])}"
    filename = f"{stem}.m4a"
    target = output_dir / filename
    if target.exists() and not force:
        print(f"skip  {target.name}", file=sys.stderr)
        return None
    if not target.exists():
        target = unique_path(output_dir, filename)

    select_and_copy_ui(memo["name"], depth=depth, ui_delay=ui_delay)
    save_clipboard_audio(target)
    if target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Export produced an empty file for {memo['name']!r}")

    print(f"saved {target.name}", file=sys.stderr)
    return target


def probe_ui(*, ui_delay: float) -> None:
    script = f"""
tell application "VoiceMemos" to activate
delay {ui_delay}
tell application "System Events"
    tell process "VoiceMemos"
        set lines to {{}}
        repeat with e in (entire contents of window 1)
            try
                set kind to class of e as text
                set label to ""
                try
                    set label to name of e
                end try
                if label is "" then
                    try
                        set label to description of e
                    end try
                end if
                if label is not "" then
                    set end of lines to kind & ": " & label
                end if
            end try
        end repeat
        set AppleScript's text item delimiters to linefeed
        return lines as text
    end tell
end tell
"""
    print(run_applescript(script))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Voice Memos to a normal folder.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output folder (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--method",
        choices=("library", "ui"),
        default="library",
        help="library copies synced files (needs Full Disk Access); ui automates the app",
    )
    parser.add_argument(
        "--sidebar-depth",
        type=int,
        help="UI method only: sidebar nesting depth (auto-detected if omitted)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="UI method only: seconds to wait between UI steps",
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
        "--probe",
        action="store_true",
        help="UI method only: dump accessible Voice Memos UI labels",
    )
    args = parser.parse_args()

    output_dir = args.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.method == "library":
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
        exported = 0
        for memo in to_export:
            try:
                if export_from_library(memo, output_dir, force=args.force):
                    exported += 1
            except RuntimeError as exc:
                print(f"Error exporting {memo['title']!r}: {exc}", file=sys.stderr)
                return 1
        print(f"Done. Exported {exported} file(s) to {output_dir}", file=sys.stderr)
        return 0

    if args.probe:
        try:
            probe_ui(ui_delay=args.delay)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    depth: int | None = args.sidebar_depth
    memos_ui: list[dict[str, str]] = []
    try:
        if depth is not None:
            memos_ui = list_from_ui(depth=depth, ui_delay=args.delay)
        else:
            try:
                depth = detect_ui_depth(ui_delay=args.delay)
                memos_ui = list_from_ui(depth=depth, ui_delay=args.delay)
            except RuntimeError:
                depth = None
                memos_ui = list_from_ui_dynamic(ui_delay=args.delay)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Tip: grant Accessibility permission, open Voice Memos → All Recordings, "
            "or use --method library with Full Disk Access.",
            file=sys.stderr,
        )
        return 1

    if not memos_ui:
        print(
            "No recordings found via UI automation. Try:\n"
            "  python export_voice_memos.py --method library --list\n"
            "  python export_voice_memos.py --method ui --probe",
            file=sys.stderr,
        )
        return 1

    if args.list:
        for memo in memos_ui:
            date_suffix = f"  ({memo['date']})" if memo.get("date") else ""
            print(f"{memo['name']}{date_suffix}")
        return 0

    to_export = memos_ui[: args.limit] if args.limit else memos_ui
    print(
        f"Exporting {len(to_export)} memo(s) via UI to {output_dir} "
        f"(depth={depth if depth is not None else 'dynamic'})...",
        file=sys.stderr,
    )
    exported = 0
    for memo in to_export:
        try:
            if export_from_ui(
                memo,
                output_dir,
                depth=depth,
                ui_delay=args.delay,
                force=args.force,
            ):
                exported += 1
        except RuntimeError as exc:
            print(f"Error exporting {memo['name']!r}: {exc}", file=sys.stderr)
            return 1
        time.sleep(args.delay)

    print(f"Done. Exported {exported} file(s) to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
