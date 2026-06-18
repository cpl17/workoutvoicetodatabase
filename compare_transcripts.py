#!/usr/bin/env python3
"""Compare OpenAI baseline transcripts with current (local) transcripts side by side."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.config import REPO_ROOT, load_config

DEFAULT_BASELINE = REPO_ROOT / "transcripts-openai"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def compare_transcripts(
    current_dir: Path,
    baseline_dir: Path,
) -> list[tuple[str, str | None, str | None]]:
    """Return (stem, baseline_text, current_text) for each memo with a current transcript."""
    results: list[tuple[str, str | None, str | None]] = []
    current_files = sorted(current_dir.glob("*.txt"), key=lambda p: p.name)

    for current_path in current_files:
        stem = current_path.stem
        baseline_path = baseline_dir / f"{stem}.txt"
        baseline_text = read_text(baseline_path) if baseline_path.is_file() else None
        current_text = read_text(current_path)
        results.append((stem, baseline_text, current_text))

    return results


def print_comparison(stem: str, baseline: str | None, current: str | None) -> None:
    print(f"{'=' * 72}")
    print(stem)
    print(f"{'-' * 72}")
    print("OpenAI (baseline):")
    print(baseline if baseline is not None else "(no baseline file)")
    print()
    print("Current:")
    print(current if current is not None else "(missing)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print OpenAI baseline vs current transcripts side by side."
    )
    parser.add_argument(
        "-c",
        "--current",
        type=Path,
        help="Current transcripts folder (default: paths.transcripts from config.yaml)",
    )
    parser.add_argument(
        "-b",
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"OpenAI baseline folder (default: {DEFAULT_BASELINE.name}/)",
    )
    args = parser.parse_args()

    config = load_config()
    current_dir = (args.current or config.paths.transcripts).expanduser().resolve()
    baseline_dir = args.baseline.expanduser().resolve()

    if not current_dir.is_dir():
        print(f"Error: current transcripts folder not found: {current_dir}", file=sys.stderr)
        return 1
    if not baseline_dir.is_dir():
        print(
            f"Error: baseline folder not found: {baseline_dir}\n"
            "Run: mkdir -p transcripts-openai && cp transcripts/*.txt transcripts-openai/",
            file=sys.stderr,
        )
        return 1

    comparisons = compare_transcripts(current_dir, baseline_dir)
    if not comparisons:
        print(f"No .txt files in {current_dir}", file=sys.stderr)
        return 0

    matched = sum(1 for _, baseline, _ in comparisons if baseline is not None)
    print(f"Comparing {len(comparisons)} memo(s) ({matched} with OpenAI baseline)\n", file=sys.stderr)

    for stem, baseline, current in comparisons:
        print_comparison(stem, baseline, current)

    same = sum(
        1
        for _, baseline, current in comparisons
        if baseline is not None and baseline == current
    )
    print(f"{'=' * 72}", file=sys.stderr)
    print(f"{same}/{matched} baseline matches are identical", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
