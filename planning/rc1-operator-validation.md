# RC1 Operator Validation (Real Long-Media)

## Source Used

The exact requested file `2024-25_Germany_-_Portugal_-_1.mp4` was **not present**
on this machine. A real long-form football match from the local archive was used
as the documented real-media source:

```
C:\FootballArchive\RAW\germany_italy_2012.mp4
```

This is a genuine full match, not a fixture or clip.

## Media Metadata (ffprobe)

- Duration: 3281.78 s (~54.7 min)
- Size: 552,988,169 bytes (~552 MB)
- Video: h264, 1024x576
- Audio: aac
- File existed, readable, with valid streams (ffprobe succeeded).

## System Preflight

`scripts/doctor.py`:

- Python 3.12.10 — PASS
- Runtime DB schema v10 — PASS
- Storage writable — PASS
- FFmpeg — PASS
- FFprobe — PASS
- faster-whisper importable — PASS
- OpenAI — WARN (not configured; non-blocking)

## Real Full Smoke

```
python scripts/smoke_real_match.py --full --input C:\FootballArchive\RAW\germany_italy_2012.mp4
```

Report classification:

```
smoke_mode = FULL
source_type = OPERATOR_MEDIA
real_media = true
```

Result: **FAIL** (honest — no fabricated PASS).

## Stage Results (real run)

| Stage | Status | Duration |
| --- | --- | --- |
| Source validation | PASS | 0.0 s |
| Transcription | REUSED | 4.6 s (reuse of real transcript) |
| Detection | BLOCKED | — (no backend) |

The full run reused a real 54.7-min transcript (5826 words / 31,654 chars,
meaningful English commentary) registered as a runtime transcript Artifact with
no duplicate artifact.

## Real Performance Metrics

- Real transcription (first run, after PyAV fix): **~22.5 min** for 54.7 min of
  commentary audio with faster-whisper `base` on CPU (int8).
- Transcript: 5826 words, 31,654 chars.
- Total real-media wall time recorded: 78.4 s (first, pre-fix run) then 4.6 s
  (reuse run).

## Defects Found and Fixed

1. **faster-whisper 1.2.1 ↔ PyAV 19.0.1 incompatibility (real blocker).**
   `faster_whisper.decode_audio` calls `av.open(..., metadata_errors="ignore")`,
   which PyAV 19.0.1 rejects with
   `TypeError: open() got an unexpected keyword argument 'metadata_errors'`.
   This blocked all real transcription.
   Fix: `pipeline/whisper_transcriber.py` now decodes audio itself via ffmpeg to
   16 kHz mono float32 and passes a `numpy` array to `model.transcribe(...)`,
   bypassing faster-whisper's PyAV decode path. Verified: real 54.7-min
   transcription succeeded. Regression tests added in
   `tests/test_whisper_transcriber.py`.

2. **Detection backend unavailable (current RC blocker).**
   Default detection provider is `ollama`; no Ollama server is running
   (`localhost:11434` connection refused) and no `OPENAI_API_KEY` is set.
   Detection cannot run, so canonical Moments cannot be produced and the full
   editorial chain (Story / Edit / EDL / Render / Variant / Export) cannot be
   exercised on real media.

## Workflow Stages Not Reached (blocked)

- Canonical Moment generation (requires detection backend)
- Moment review in Console on real moments
- Story build / approve
- Generate Edit / Prepare Cut
- Generate Rough Cut / review
- TikTok variant / export

## Retry / Reuse Verified

- After the PyAV fix, the transcript was reused on the second real run
  (no re-transcription, no duplicate transcript artifact).

## Remaining Blocker

```
Real long-media workflow = BLOCKED
Reason: Ollama is not installed on the validation machine (OLLAMA_NOT_INSTALLED)
```

To complete: install Ollama, pull the configured model (`llama3.1`), start the
service, then re-run the real full smoke. Detection, Story, and Edit Brief all
support Ollama now, so a running Ollama unblocks the full editorial chain. OpenAI
(`OPENAI_API_KEY`) remains an alternative for any stage.

## Provider Readiness (Unblock Work)

Added `pipeline/provider_service.py`:

- Detection provider readiness for Ollama and OpenAI (no live paid calls).
- Story/Edit generation readiness (OpenAI only in current code).
- Safe `DETECTION_PROVIDER_UNAVAILABLE` / `STORY_PROVIDER_UNAVAILABLE` codes.
- Operator-safe message: "Detection is unavailable. No model provider is
  currently ready. Start Ollama or configure OpenAI, then retry."

Wired into:

- `operator_console.analyze_project` preflight (after transcription, so the
  transcript is always preserved and reused on retry).
- Doctor and the `/system` page (Detection / Story provider status).
- Project detail recovery action: "Detection blocked — your transcript is saved.
  [Retry Analysis] [Run System Check]".

Re-run result on the real match: transcription **REUSED** (real 54.7-min
transcript, single artifact), detection **BLOCKED** with the safe provider code.
No provider secrets are surfaced.

## Ollama Readiness (This Gate)

- Configured detection provider: `ollama`
- Ollama endpoint: default `http://localhost:11434/api/generate` (`OLLAMA_URL` unset)
- Configured Ollama model: `llama3.1` (from `OLLAMA_MODEL` env default)
- `OLLAMA_MODEL` env: unset (code default used)
- **Ollama binary: NOT INSTALLED** (`OLLAMA_NOT_INSTALLED`; `Get-Command ollama` returns nothing)

Blocker: `OLLAMA_NOT_INSTALLED`.

Next operator manual actions to unblock detection:

```
1. Install Ollama for Windows (https://ollama.com/download or `winget install Ollama.Ollama`)
2. Pull the configured model:  ollama pull llama3.1
3. Start the service:          ollama serve   (or launch the Ollama app)
4. Re-run the real full smoke:
   clipper-smoke --full --input C:\FootballArchive\RAW\germany_italy_2012.mp4
```

Alternative: set `OPENAI_API_KEY` (detection also supports OpenAI). Story/Edit
generation requires OpenAI regardless.

Expected detection state after Ollama is ready:

```
Detection Provider   Ready via Ollama
```

Expected smoke progression:

```
Source validation      PASS
Transcription          REUSED
Detection              PASS
Moment normalization   PASS
```

No retranscription: the preserved real transcript at
`TRANSCRIPTS/WORLD_CUP/germany_italy_2012/transcript.txt` will be reused.

## Provider Completion — Ollama for Story + Edit (Preserving OpenAI)

All model-dependent editorial stages now support Ollama OR OpenAI, selected per
stage. OpenAI remains the default and is unchanged.

| Stage | Provider selection | Default |
| --- | --- | --- |
| Transcription | faster-whisper (local) | — |
| Detection | `providers.detection` (ollama | openai) | `ollama` |
| Story | `STORY_PROVIDER` | `openai` |
| Edit Brief | `EDIT_PROVIDER` (default = `STORY_PROVIDER`) | `openai` |

Implementation:

- `pipeline/provider_service.py` — shared `ollama_generate()` helper
  (`OLLAMA_URL` + `OLLAMA_MODEL`, optional `OLLAMA_STORY_MODEL` /
  `OLLAMA_EDIT_MODEL` overrides, JSON mode), plus per-stage readiness for
  Detection / Story / Edit.
- `story_engine._run_story_llm` and `edit_brief._run_brief_llm` select the
  provider from the stage env; Ollama uses the shared helper, OpenAI uses the
  unchanged existing path. Story / Edit artifact formats and parser contracts
  are identical regardless of provider.
- `operator_console.generate_stories` and `generate_canonical_edit_brief`
  preflight the selected provider and fail safely
  (`STORY_PROVIDER_UNAVAILABLE` / `EDIT_PROVIDER_UNAVAILABLE`) without touching
  preserved Moments / approved Story.
- Doctor and the Console `/system` Providers card report Detection / Story /
  Edit independently. A fully-Ollama workflow shows PASS and does not warn about
  a missing OpenAI key.

Status on the validation machine:

- Ollama binary: **NOT INSTALLED** (`OLLAMA_NOT_INSTALLED`) — the real chain
  cannot run here yet.
- Code + automated tests for the fully-local path (`faster-whisper + Ollama +
  FFmpeg`) are complete: `tests/test_provider_story_edit.py` (20 tests) and
  `tests/test_provider_service.py`.

Updated operator manual actions (unblocks detection AND Story/Edit):

```
1. Install Ollama for Windows (https://ollama.com/download or `winget install Ollama.Ollama`)
2. Pull the configured model:  ollama pull llama3.1
3. Start the service:          ollama serve   (or launch the Ollama app)
4. (Optional) set STORY_PROVIDER=ollama EDIT_PROVIDER=ollama for a fully-local chain
5. Re-run the real full smoke:
   clipper-smoke --full --input C:\FootballArchive\RAW\germany_italy_2012.mp4
```

Expected doctor state after Ollama is ready (fully-local config):

```
Detection provider   PASS — Ollama / llama3.1
Story provider       PASS — Ollama / llama3.1
Edit provider        PASS — Ollama / llama3.1
```