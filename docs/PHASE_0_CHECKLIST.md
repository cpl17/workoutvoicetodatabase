# Phase 0 Checklist — Foundation

Work through these in order. Each step is one bite-sized session. Check boxes as you go.
Ask questions anytime — that's the point.

**Goal of Phase 0:** replace "does a `.txt` file exist?" as state tracking with an explicit SQLite manifest, a single pipeline entry point, and local Whisper transcription.

**You already have:** memos in `voice-memos/` and matching transcripts in `transcripts/` (count grows as you record).

**Export:** `[export_voice_memos.py](../export_voice_memos.py)` is **library-only** — it reads Apple's `CloudRecordings.db` and copies synced `.m4a` files. Requires Full Disk Access for Terminal/Cursor. (UI/AppleScript export was removed.)

---

## Phase 0a — Manifest, config, pipeline shell

### Step 0a.1 — Understand what we're fixing

- [x] Read `[export_voice_memos.py](../export_voice_memos.py)` — library export from `CloudRecordings.db`, filename format, skip-if-exists logic, `apple_recording_path` (ZPATH)
- [x] Read `[transcribe_voice_memos.py](../transcribe_voice_memos.py)` and note how it decides what's "pending" (transcript file missing)
- [x] List your memos: `ls voice-memos/` and `ls transcripts/`
- [x] Try listing memos from Apple's DB: `python export_voice_memos.py --list`

**Why:** today each script tracks state independently. The manifest becomes the single source of truth.

**Question to consider:** what happens if you rename a file, re-export with `--force`, or transcribe twice?

**Answer (2026-06-18 — pre-manifest behavior):**

Audio and transcripts are linked only by **matching filenames** (`foo.m4a` ↔ `foo.txt`). Nothing tracks stable identity yet.


| Action                              | Export                                                                         | Transcribe                                                  | Risk                                   |
| ----------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------- | -------------------------------------- |
| Rename audio in `voice-memos/`      | Next export uses Apple's DB name, ignores your rename                          | Looks for `transcripts/{new-stem}.txt`; old `.txt` orphaned | Duplicates, wasted API                 |
| Rename transcript in `transcripts/` | —                                                                              | Audio stem no longer matches → re-transcribes               | Wasted API                             |
| Re-export with `--force`            | Overwrites file if canonical name exists; writes fresh copy if you had renamed | —                                                           | Duplicate audio if you renamed exports |
| Transcribe twice (no `--force`)     | —                                                                              | Skipped if `.txt` exists                                    | Safe                                   |
| Transcribe twice (`--force`)        | —                                                                              | Overwrites `.txt`, calls API again                          | Cost                                   |


**Why the manifest fixes this (Phase 0a+):** key memos by `apple_recording_path` (ZPATH), not filename. Re-export updates `audio_path` on the same row; transcribe checks `transcribe_status` instead of "does `.txt` exist?"; orphans become detectable.

---

### Step 0a.2 — Add project config

- [x] Create `[config.yaml](../config.yaml)` with paths and placeholders (transcription/parsing sections can be stubs for now)
- [x] Add `pyyaml` to `[requirements.txt](../requirements.txt)` and install: `pip install pyyaml`
- [x] Create `lib/` package: `lib/__init__.py` + `lib/config.py` that loads and validates config

**Verify:**

```bash
python -c "from lib.config import load_config; print(load_config())"
```

**Learn:** why externalize paths/settings instead of hardcoding `Path("voice-memos")` everywhere?

**Answer:** One file (`config.yaml`) holds paths and future settings (Whisper backend, parse model). Scripts call `load_config()` instead of scattering defaults — change once, applies everywhere. Validation at load time catches typos early instead of failing mid-pipeline.

---

### Step 0a.3 — Design the manifest schema

- [x] Create `[lib/manifest.py](../lib/manifest.py)` with a `Manifest` class
- [x] Implement `init_db()` — create SQLite at `data/manifest.db`
- [x] Implement `memos` table (start with core columns only):


| Column                     | Purpose                                   |
| -------------------------- | ----------------------------------------- |
| `id`                       | UUID primary key                          |
| `apple_recording_path`     | `ZPATH` from Apple DB (stable id)         |
| `recorded_at`              | ISO timestamp                             |
| `title`                    | memo title                                |
| `audio_path`               | relative path, e.g. `voice-memos/foo.m4a` |
| `transcript_path`          | nullable                                  |
| `export_status`            | `pending` / `done` / `failed`             |
| `transcribe_status`        | `pending` / `done` / `failed` / `skipped` |
| `created_at`, `updated_at` | audit                                     |


- [x] Add helper methods: `upsert_memo(...)`, `get_memo_by_apple_path(...)`, `list_memos(...)`, `update_status(...)`

**Verify:**

```bash
python -c "from lib.manifest import Manifest; m = Manifest('data/manifest.db'); m.init_db(); print('ok')"
```

**Learn:** why SQLite over a JSON file for concurrent updates and queries?

**Answer:** SQLite handles many small updates safely, supports queries (`transcribe_status=pending`), and won't corrupt if two steps write at once. JSON would require read-modify-write the whole file each time.

---

### Step 0a.4 — Backfill manifest from existing files

- [x] Create `backfill_manifest.py` (one-time migration script; local only)
- [x] Scan `voice-memos/*.m4a` — for each file, parse timestamp + title from filename
- [x] Insert a manifest row with `export_status=done`, `transcribe_status=pending`
- [x] If matching `transcripts/{stem}.txt` exists, set `transcript_path` and `transcribe_status=done`

**Verify:**

```bash
python backfill_manifest.py
python -c "from lib.manifest import Manifest; m=Manifest('data/manifest.db'); print(len(m.list_memos()))"
```

---

### Step 0a.5 — Wire export → manifest

- [x] After a successful copy in `export_from_library()`, call `manifest.upsert_memo(...)` with `export_status=done` and `apple_recording_path` from `list_from_library()`
- [x] On skip (file already exists), still ensure manifest row exists (upsert from DB metadata)
- [x] Add optional `--no-manifest` flag to export for debugging without DB writes

**Verify:**

```bash
python export_voice_memos.py --list
python export_voice_memos.py             # skips existing; saves new memos only
python -c "from lib.manifest import Manifest; m=Manifest.from_config(); print(len(m.list_memos()))"
```

**Learn:** `ZPATH` (`apple_recording_path`) is the stable key — filename can drift, Apple path shouldn't.

**Answer:** Export now writes manifest rows keyed by ZPATH. Skips still sync manifest (preserving `transcribe_status`). New memos get `export_status=done`, `transcribe_status=pending`.

---

### Step 0a.6 — Wire transcribe → manifest

- [x] Update `[transcribe_voice_memos.py](../transcribe_voice_memos.py)` to query manifest for `transcribe_status=pending` instead of scanning for missing `.txt`
- [x] After transcribing, update manifest: `transcript_path`, `transcribe_status=done`
- [x] On failure, set `transcribe_status=failed` + `error_message`

**Verify:**

```bash
python export_voice_memos.py               # new memo → manifest row with transcribe_status=pending
python transcribe_voice_memos.py --dry-run   # lists pending memos only (not all missing .txt)
python transcribe_voice_memos.py             # writes transcript; sets transcribe_status=done
python transcribe_voice_memos.py --dry-run   # expect: 0 pending when caught up
```

**Learn:** manifest-driven pending list survives even if transcript files are deleted (you can detect inconsistency).

**Answer:** Transcribe reads `transcribe_status=pending` from the manifest (paths from `config.yaml`). On success it sets `transcript_path` and `transcribe_status=done`; on failure, `transcribe_status=failed` plus `error_message`. Verified end-to-end with new memos (export → pending row → transcribe → done).

---

### Step 0a.7 — Create `run_pipeline.py`

- [x] Create `[run_pipeline.py](../run_pipeline.py)` orchestrator
- [x] Accept `--stage export|transcribe|all`, `--dry-run`, `--force`
- [x] Load config, init manifest, call export then transcribe stages
- [x] Print human-readable summary to stderr
- [x] Print **JSON summary** as last line of stdout (for future Hermes):

```json
{"status":"ok","exported":0,"transcribed":0,"errors":[]}
```

**Verify:**

```bash
python run_pipeline.py --stage all --dry-run
python run_pipeline.py --stage all
```

**Answer:** `run_pipeline.py` orchestrates export then transcribe using shared library functions and the manifest. Human-readable progress goes to stderr; the last stdout line is JSON for automation.

**Phase 0a done when:**

- [x] `data/manifest.db` exists with memos, all `export_status=done`
- [x] `python run_pipeline.py --stage all` runs cleanly (0 new work)
- [x] You can explain what each table column means

---

## Phase 0b — Local Whisper + comparison

### Step 0b.1 — Understand the transcription swap

- [x] Read current `[transcribe.py](../transcribe.py)` — note it calls OpenAI `whisper-1` API
- [x] Read OpenAI transcripts in `transcripts/` — these become your **baseline** for comparison

**Why:** Phase 0b replaces cloud transcription with local `faster-whisper`. OpenAI transcripts stay on disk as reference (don't delete yet).

**Question to consider:** local Whisper sends no audio to the cloud, but parsing (Phase 1) still will. Cool with that split?

**Answer (2026-06-18):**

**Current call chain:** `run_pipeline.py` / `transcribe_voice_memos.py` → `transcribe.transcribe()` → OpenAI `client.audio.transcriptions.create(model="whisper-1")`. Requires `OPENAI_API_KEY`; uploads the full `.m4a` on every call. Language comes from CLI or `config.yaml` (`transcription.language`).

**Baseline transcripts (8 memos, all OpenAI):** gym-style memos (`E Boston St`, squats/bench with sets×reps×weight) transcribe well; `Iron Works Fitness` is a long rep count; `Save A Lot` is noisy/wrong (likely background audio — the hard comparison case); `I-84 E` and `New Recording` are non-workout tests.

**Cloud vs local split:** transcription moves local in 0b; Phase 1 parsing still sends *text* (not audio) to an LLM. Audio stays on disk; only derived transcript text goes to the cloud for structured extraction.

---

### Step 0b.2 — Install faster-whisper

- [x] Add `faster-whisper` to `[requirements.txt](../requirements.txt)`
- [x] Install: `pip install faster-whisper`
- [x] First run downloads the `small` model (~500MB) — expect a wait (happened in 0b.3 verify)

**Verify:**

```bash
python -c "from faster_whisper import WhisperModel; print('import ok')"
```

**Learn:** `small` is fast but less accurate; you'll upgrade later. Model name goes in config + manifest.

**Answer:** Installed `faster-whisper` 1.2.1 in `.venv` (pulls in `ctranslate2`, `onnxruntime`, etc.). Import verified; model download deferred until first local transcribe in 0b.3.

---

### Step 0b.3 — Create Whisper backend abstraction

- [x] Create `lib/whisper/__init__.py` and `[lib/whisper/backends.py](../lib/whisper/backends.py)`
- [x] Define a simple interface:

```python
def transcribe(audio_path: Path, *, language: str | None = None) -> str: ...
```

- [x] Implement `FasterWhisperBackend` — loads model from `config.yaml` once, reuses across files
- [x] Implement `OpenAIBackend` — wrap existing OpenAI logic (keep for comparison)

**Verify:**

```bash
python -c "
from pathlib import Path
from lib.whisper.backends import FasterWhisperBackend
b = FasterWhisperBackend(model='small', language='en')
print(b.transcribe(Path('voice-memos/2026-05-28 18.25.25 New Recording.m4a')))
"
```

**Learn:** backend abstraction lets you A/B test without changing `transcribe_voice_memos.py` logic.

**Answer:** `TranscriptionBackend` protocol plus `OpenAIBackend` and `FasterWhisperBackend`. Local backend loads `WhisperModel` once in `__init__` and reuses it. Verify on "New Recording" matched OpenAI baseline ("Testing, testing…").

---

### Step 0b.4 — Update config for transcription backend

- [ ] Fill in `config.yaml` transcription section:

```yaml
transcription:
  backend: faster_whisper   # faster_whisper | openai
  model: small
  language: en
```

- [ ] Update `[transcribe.py](../transcribe.py)` to delegate to configured backend
- [ ] Store `transcribe_backend` + `transcribe_model` on manifest row when transcribing

**Verify:**

```bash
python transcribe.py "voice-memos/2026-05-28 18.25.25 New Recording.m4a"
```

---

### Step 0b.5 — Side-by-side comparison setup

- [ ] Create `transcripts-openai/` — copy current OpenAI transcripts there as baseline:

```bash
mkdir -p transcripts-openai
cp transcripts/*.txt transcripts-openai/
```

- [ ] Add `transcripts-openai/` to `[.gitignore](../.gitignore)` (optional, or keep as reference)
- [ ] Create `[compare_transcripts.py](../compare_transcripts.py)` — for each memo, print OpenAI vs local Whisper side by side

**Verify:**

```bash
python compare_transcripts.py
```

**Learn:** don't assume local is better on every memo — compare on your real gym audio.

---

### Step 0b.6 — Re-transcribe all with local Whisper

- [ ] Reset manifest transcribe status (or use `--force`):

```bash
python transcribe_voice_memos.py --force
# or: python run_pipeline.py --stage transcribe --force
```

- [ ] Confirm manifest rows show `transcribe_backend=faster_whisper`, `transcribe_model=small`
- [ ] Run `compare_transcripts.py` and review all 4 memos
- [ ] Pay special attention to `[2026-05-28 15.13.15 Save A Lot](../transcripts/2026-05-28%2015.13.15%20Save%20A%20Lot.txt)` — the hard one

**Questions to answer after comparing:**

- [ ] Is local Whisper good enough for Phase 1 parsing?
- [ ] Do you want to try `small.en` or `medium` before moving on?
- [ ] Any memos where OpenAI was clearly better?

---

### Step 0b.7 — Clean up and document decisions

- [ ] Update `[.gitignore](../.gitignore)` to include `data/`
- [ ] Add `transcribe_backend` / `transcribe_model` columns to manifest if not already there
- [ ] Write 2–3 sentences in a comment or note: which backend you chose and why

**Phase 0b done when:**

- [ ] All 4 memos transcribed locally via faster-whisper
- [ ] You've compared against OpenAI baseline and made a conscious model choice
- [ ] `python run_pipeline.py --stage all` uses local Whisper by default
- [ ] Manifest records which backend transcribed each memo

---

## Quick reference — files created/modified


| File                        | Phase | Action                               |
| --------------------------- | ----- | ------------------------------------ |
| `config.yaml`               | 0a    | Create                               |
| `lib/config.py`             | 0a    | Create                               |
| `lib/manifest.py`           | 0a    | Create                               |
| `backfill_manifest.py`      | 0a    | Create                               |
| `run_pipeline.py`           | 0a    | Create                               |
| `export_voice_memos.py`     | 0a    | Modify (wire manifest; library-only) |
| `transcribe_voice_memos.py` | 0a    | Modify                               |
| `lib/whisper/backends.py`   | 0b    | Create                               |
| `transcribe.py`             | 0b    | Modify                               |
| `compare_transcripts.py`    | 0b    | Create                               |
| `requirements.txt`          | 0a/0b | Modify                               |
| `.gitignore`                | 0b    | Modify                               |


---

## How we'll work

1. Pick the next unchecked step.
2. You ask questions — we implement or you implement with guidance.
3. Run the **Verify** commands together.
4. Check the box and move on.

**Ready to start?** Say "let's do 0a.1" (or jump to whichever step you want).