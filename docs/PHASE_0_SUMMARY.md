# Phase 0 Summary — Foundation

Completed June 2026. Phase 0 replaced filename-based state tracking with an explicit SQLite manifest, a single pipeline entry point, and local Whisper transcription.

**Goal achieved:** no longer rely on "does a `.txt` file exist?" — the manifest is the source of truth, `run_pipeline.py` orchestrates export → transcribe, and transcription runs locally via `faster_whisper`/`small` by default.

**Export:** `[export_voice_memos.py](../export_voice_memos.py)` is **library-only** — reads Apple's `CloudRecordings.db` and copies synced `.m4a` files. Requires Full Disk Access for Terminal/Cursor.

---

## Phase 0a — Manifest, config, pipeline shell

### 0a.1 — Understand what we fixed

- Read `[export_voice_memos.py](../export_voice_memos.py)` — library export from `CloudRecordings.db`, filename format, skip-if-exists logic, `apple_recording_path` (ZPATH)
- Read `[transcribe_voice_memos.py](../transcribe_voice_memos.py)` and note how it decides what's "pending" (transcript file missing)
- List your memos: `ls voice-memos/` and `ls transcripts/`
- Try listing memos from Apple's DB: `python export_voice_memos.py --list`

**Why we did this:** each script tracked state independently; the manifest is now the single source of truth.

**Context (pre-manifest, 2026-06-18):**

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

### 0a.2 — Add project config

- Create `[config.yaml](../config.yaml)` with paths and placeholders (transcription/parsing sections can be stubs for now)
- Add `pyyaml` to `[requirements.txt](../requirements.txt)` and install: `pip install pyyaml`
- Create `lib/` package: `lib/__init__.py` + `lib/config.py` that loads and validates config

**Commands:**

```bash
python -c "from lib.config import load_config; print(load_config())"
```

**Note:** why externalize paths/settings instead of hardcoding `Path("voice-memos")` everywhere?

**Summary:** One file (`config.yaml`) holds paths and future settings (Whisper backend, parse model). Scripts call `load_config()` instead of scattering defaults — change once, applies everywhere. Validation at load time catches typos early instead of failing mid-pipeline.

---

### 0a.3 — Design the manifest schema

- Create `[lib/manifest.py](../lib/manifest.py)` with a `Manifest` class
- Implement `init_db()` — create SQLite at `data/manifest.db`
- Implement `memos` table (start with core columns only):


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


- Add helper methods: `upsert_memo(...)`, `get_memo_by_apple_path(...)`, `list_memos(...)`, `update_status(...)`

**Commands:**

```bash
python -c "from lib.manifest import Manifest; m = Manifest('data/manifest.db'); m.init_db(); print('ok')"
```

**Note:** why SQLite over a JSON file for concurrent updates and queries?

**Summary:** SQLite handles many small updates safely, supports queries (`transcribe_status=pending`), and won't corrupt if two steps write at once. JSON would require read-modify-write the whole file each time.

---

### 0a.4 — Backfill manifest from existing files

- Create `backfill_manifest.py` (one-time migration script; local only)
- Scan `voice-memos/*.m4a` — for each file, parse timestamp + title from filename
- Insert a manifest row with `export_status=done`, `transcribe_status=pending`
- If matching `transcripts/{stem}.txt` exists, set `transcript_path` and `transcribe_status=done`

**Commands:**

```bash
python backfill_manifest.py
python -c "from lib.manifest import Manifest; m=Manifest('data/manifest.db'); print(len(m.list_memos()))"
```

---

### 0a.5 — Wire export → manifest

- After a successful copy in `export_from_library()`, call `manifest.upsert_memo(...)` with `export_status=done` and `apple_recording_path` from `list_from_library()`
- On skip (file already exists), still ensure manifest row exists (upsert from DB metadata)
- Add optional `--no-manifest` flag to export for debugging without DB writes

**Commands:**

```bash
python export_voice_memos.py --list
python export_voice_memos.py             # skips existing; saves new memos only
python -c "from lib.manifest import Manifest; m=Manifest.from_config(); print(len(m.list_memos()))"
```

**Note:** `ZPATH` (`apple_recording_path`) is the stable key — filename can drift, Apple path shouldn't.

**Summary:** Export now writes manifest rows keyed by ZPATH. Skips still sync manifest (preserving `transcribe_status`). New memos get `export_status=done`, `transcribe_status=pending`.

---

### 0a.6 — Wire transcribe → manifest

- Update `[transcribe_voice_memos.py](../transcribe_voice_memos.py)` to query manifest for `transcribe_status=pending` instead of scanning for missing `.txt`
- After transcribing, update manifest: `transcript_path`, `transcribe_status=done`
- On failure, set `transcribe_status=failed` + `error_message`

**Commands:**

```bash
python export_voice_memos.py               # new memo → manifest row with transcribe_status=pending
python transcribe_voice_memos.py --dry-run   # lists pending memos only (not all missing .txt)
python transcribe_voice_memos.py             # writes transcript; sets transcribe_status=done
python transcribe_voice_memos.py --dry-run   # expect: 0 pending when caught up
```

**Note:** manifest-driven pending list survives even if transcript files are deleted (you can detect inconsistency).

**Summary:** Transcribe reads `transcribe_status=pending` from the manifest (paths from `config.yaml`). On success it sets `transcript_path` and `transcribe_status=done`; on failure, `transcribe_status=failed` plus `error_message`. Verified end-to-end with new memos (export → pending row → transcribe → done).

---

### 0a.7 — Create `run_pipeline.py`

- Create `[run_pipeline.py](../run_pipeline.py)` orchestrator
- Accept `--stage export|transcribe|all`, `--dry-run`, `--force`
- Load config, init manifest, call export then transcribe stages
- Print human-readable summary to stderr
- Print **JSON summary** as last line of stdout (for future Hermes):

```json
{"status":"ok","exported":0,"transcribed":0,"errors":[]}
```

**Commands:**

```bash
python run_pipeline.py --stage all --dry-run
python run_pipeline.py --stage all
```

**Summary:** `run_pipeline.py` orchestrates export then transcribe using shared library functions and the manifest. Human-readable progress goes to stderr; the last stdout line is JSON for automation.

**Outcome:**

- `data/manifest.db` exists with memos, all `export_status=done`
- `python run_pipeline.py --stage all` runs cleanly (0 new work)
- You can explain what each table column means

---

## Phase 0b — Local Whisper + comparison

### 0b.1 — Understand the transcription swap

- Read current `[transcribe.py](../transcribe.py)` — note it calls OpenAI `whisper-1` API
- Read OpenAI transcripts in `transcripts/` — these become your **baseline** for comparison

**Why we did this:** Phase 0b moved transcription to local `faster-whisper`. OpenAI transcripts were kept on disk as a comparison baseline (`transcripts-openai/`).

**Decision:** local Whisper for transcription; Phase 1 parsing still sends text (not audio) to an LLM.

**Summary (2026-06-18):**

**Current call chain:** `run_pipeline.py` / `transcribe_voice_memos.py` → `transcribe.transcribe()` → OpenAI `client.audio.transcriptions.create(model="whisper-1")`. Requires `OPENAI_API_KEY`; uploads the full `.m4a` on every call. Language comes from CLI or `config.yaml` (`transcription.language`).

**Baseline transcripts (8 memos, all OpenAI):** gym-style memos (`E Boston St`, squats/bench with sets×reps×weight) transcribe well; `Iron Works Fitness` is a long rep count; `Save A Lot` is noisy/wrong (likely background audio — the hard comparison case); `I-84 E` and `New Recording` are non-workout tests.

**Cloud vs local split:** transcription moves local in 0b; Phase 1 parsing still sends *text* (not audio) to an LLM. Audio stays on disk; only derived transcript text goes to the cloud for structured extraction.

---

### 0b.2 — Install faster-whisper

- Add `faster-whisper` to `[requirements.txt](../requirements.txt)`
- Install: `pip install faster-whisper`
- First run downloads the `small` model (~500MB) — expect a wait (happened in 0b.3 verify)

**Commands:**

```bash
python -c "from faster_whisper import WhisperModel; print('import ok')"
```

**Note:** `small` is fast but less accurate; you'll upgrade later. Model name goes in config + manifest.

**Summary:** Installed `faster-whisper` 1.2.1 in `.venv` (pulls in `ctranslate2`, `onnxruntime`, etc.). Import verified; model download deferred until first local transcribe in 0b.3.

---

### 0b.3 — Create Whisper backend abstraction

- Create `lib/whisper/__init__.py` and `[lib/whisper/backends.py](../lib/whisper/backends.py)`
- Define a simple interface:

```python
def transcribe(audio_path: Path, *, language: str | None = None) -> str: ...
```

- Implement `FasterWhisperBackend` — loads model from `config.yaml` once, reuses across files
- Implement `OpenAIBackend` — wrap existing OpenAI logic (keep for comparison)

**Commands:**

```bash
python -c "
from pathlib import Path
from lib.whisper.backends import FasterWhisperBackend
b = FasterWhisperBackend(model='small', language='en')
print(b.transcribe(Path('voice-memos/2026-05-28 18.25.25 New Recording.m4a')))
"
```

**Note:** backend abstraction lets you A/B test without changing `transcribe_voice_memos.py` logic.

**Summary:** `TranscriptionBackend` protocol plus `OpenAIBackend` and `FasterWhisperBackend`. Local backend loads `WhisperModel` once in `__init__` and reuses it. Verify on "New Recording" matched OpenAI baseline ("Testing, testing…").

---

### 0b.4 — Update config for transcription backend

- Fill in `config.yaml` transcription section:

```yaml
transcription:
  backend: faster_whisper   # faster_whisper | openai
  model: small
  language: en
```

- Update `[transcribe.py](../transcribe.py)` to delegate to configured backend
- Store `transcribe_backend` + `transcribe_model` on manifest row when transcribing

**Commands:**

```bash
python transcribe.py "voice-memos/2026-05-28 18.25.25 New Recording.m4a"
```

**Summary:** Default backend is `faster_whisper`/`small`. `transcribe.py` caches the backend from config; OpenAI API key required only when `backend: openai`. Manifest rows get `transcribe_backend` and `transcribe_model` on successful transcribe (batch + pipeline).

---

### 0b.5 — Side-by-side comparison setup

- Create `transcripts-openai/` — copy current OpenAI transcripts there as baseline:

```bash
mkdir -p transcripts-openai
cp transcripts/*.txt transcripts-openai/
```

- Add `transcripts-openai/` to `[.gitignore](../.gitignore)` (optional, or keep as reference)
- Create `[compare_transcripts.py](../compare_transcripts.py)` — for each memo, print OpenAI vs local Whisper side by side

**Commands:**

```bash
python compare_transcripts.py
```

**Note:** don't assume local is better on every memo — compare on your real gym audio.

**Summary:** Baseline copied (8 `.txt` files) to `transcripts-openai/` (gitignored). `compare_transcripts.py` pairs by filename stem and prints OpenAI vs current. All 8 match today because `transcripts/` still holds OpenAI output — differences will show after 0b.6 re-transcribe.

---

### 0b.6 — Re-transcribe all with local Whisper

- Reset manifest transcribe status (or use `--force`):

```bash
python transcribe_voice_memos.py --force
# or: python run_pipeline.py --stage transcribe --force
```

- Confirm manifest rows show `transcribe_backend=faster_whisper`, `transcribe_model=small`
- Run `compare_transcripts.py` and review all memos
- Pay special attention to `[2026-05-28 15.13.15 Save A Lot](../transcripts/2026-05-28%2015.13.15%20Save%20A%20Lot.txt)` — the hard one

**Summary:** Re-transcribed 8 memos via `run_pipeline.py --stage transcribe --force`. All manifest rows: `faster_whisper` / `small`.

**Comparison conclusions:** `small` is good enough for Phase 1 on typical gym memos. No upgrade to `small.en` or `medium` yet. OpenAI was clearly better only on Iron Works Fitness (local returned empty).

| Memo | Verdict |
|------|---------|
| E Boston St 2/3/4 | Local good — sets/reps/weights captured; minor formatting (lowercase, "eight" vs "8") |
| E Boston St (May 28) | Both usable; OpenAI slightly cleaner on weights ("2.5" vs "2.5 seconds") |
| New Recording / I-84 E | Equivalent |
| **Save A Lot** | **Local better** — OpenAI hallucinated YouTube outro; local got workout-like sets/reps |
| **Iron Works Fitness** | **OpenAI better** — local returned **empty** transcript; OpenAI captured rep count |

**Decisions:** `small` is good enough to proceed to Phase 1 for typical gym memos. No model upgrade yet — retry Iron Works Fitness if rep-count memos matter; consider `medium` only if that memo stays empty. `small.en` optional later for English-only speed bump.

---

### 0b.7 — Clean up and document decisions

- Update `[.gitignore](../.gitignore)` to include `data/`
- Add `transcribe_backend` / `transcribe_model` columns to manifest if not already there
- Write 2–3 sentences in a comment or note: which backend you chose and why

**Summary:** `data/` was already gitignored. Manifest columns added in 0b.4. Decision documented in `config.yaml` comments: `faster_whisper`/`small` for local, private transcription; OpenAI kept as config switch for comparison.

**Outcome:**

- All memos transcribed locally via faster-whisper
- You've compared against OpenAI baseline and made a conscious model choice
- `python run_pipeline.py --stage all` uses local Whisper by default
- Manifest records which backend transcribed each memo

---

## Files created or modified


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

