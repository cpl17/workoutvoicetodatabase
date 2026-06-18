#!/usr/bin/env python3
"""Mine candidate exercises from existing transcripts using OpenAI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from lib.config import REPO_ROOT, load_config
from lib.exercises import ExerciseCatalog, slugify
from lib.manifest import Manifest


def _read_transcript(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _collect_transcripts(manifest: Manifest, transcripts_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for memo in manifest.list_memos(transcribe_status="done"):
        if not memo.transcript_path:
            continue
        transcript_path = Path(memo.transcript_path)
        if not transcript_path.is_absolute():
            transcript_path = REPO_ROOT / transcript_path
        if not transcript_path.is_file():
            print(
                f"warn  skipping {memo.title!r}: transcript not found ({memo.transcript_path})",
                file=sys.stderr,
            )
            continue
        text = _read_transcript(transcript_path)
        if not text:
            continue
        rows.append(
            {
                "memo_id": memo.id,
                "title": memo.title,
                "transcript": text,
            }
        )
    return rows


def _propose_exercises(model: str, transcripts: list[dict[str, str]]) -> list[dict[str, object]]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    payload = json.dumps(transcripts, indent=2)
    system = (
        "You analyze gym voice memo transcripts and propose strength-training exercises. "
        "Return JSON with key exercises: an array of objects with display_name (string), "
        "aliases (array of strings heard in speech), and evidence (short string). "
        "Include only plausible gym exercises. Merge duplicates."
    )
    user = f"Transcripts:\n{payload}"
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned empty bootstrap response")
    data = json.loads(content)
    exercises = data.get("exercises", [])
    if not isinstance(exercises, list):
        raise RuntimeError("Bootstrap response exercises must be a list")
    return [item for item in exercises if isinstance(item, dict)]


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Bootstrap candidate exercises from existing transcripts."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed candidates without writing exercises.json",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: set OPENAI_API_KEY in your environment or in a .env file.", file=sys.stderr)
        return 1

    config = load_config()
    manifest = Manifest.from_config()
    manifest.init_db()

    transcripts = _collect_transcripts(manifest, config.paths.transcripts)
    if not transcripts:
        print("No transcripts found to bootstrap from.", file=sys.stderr)
        return 0

    print(f"Bootstrapping from {len(transcripts)} transcript(s)...", file=sys.stderr)
    proposed = _propose_exercises(config.parsing.model, transcripts)

    catalog = ExerciseCatalog.load()
    added = 0
    for item in proposed:
        display_name = str(item.get("display_name", "")).strip()
        if not display_name:
            continue
        aliases_raw = item.get("aliases", [])
        aliases = (
            [str(alias) for alias in aliases_raw]
            if isinstance(aliases_raw, list)
            else [display_name]
        )
        ex_id = slugify(display_name)
        if catalog.get(ex_id) is not None:
            continue
        catalog.add_candidate(
            display_name=display_name,
            aliases=aliases,
            source="bootstrap",
            exercise_id=ex_id,
        )
        added += 1
        print(f"  candidate: {display_name} ({ex_id})", file=sys.stderr)

    if args.dry_run:
        print(f"[dry-run] Would add {added} candidate exercise(s)", file=sys.stderr)
        return 0

    catalog.save()
    print(f"Wrote {added} new candidate exercise(s) to exercises.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
