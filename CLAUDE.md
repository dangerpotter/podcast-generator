# CLAUDE.md

Project memory for the Capella Course Podcast Generator. This file is the durable, always-loaded spec summary and the authoritative reference for project decisions.

## What this is

A fully local, offline app. It ingests a Capella course content JSON export and, for every module, generates a Summary Report (DOCX), a Podcast Script (DOCX) from that report, and a Podcast (MP3) from that script. Supports an edit-and-regenerate loop.

## Non-negotiables

- Local only. No cloud LLM or TTS calls, ever. No API keys. After the model is cached, a full run makes zero network calls.
- Do NOT use Ollama. The LLM runs embedded in-process.
- Never fabricate course facts. The LLM summarizes and reorganizes the source content only. It must not invent dates, grade weights, resources, or links.

## Stack and pinned choices

- Language: Python.
- LLM: Gemma 4 12B Unified, instruction-tuned, run through embedded llama.cpp via `llama-cpp-python` (in-process, no daemon). Default weights: `unsloth/gemma-4-12B-it-qat-GGUF`. NOTE (verified 2026-06): that repo's only main weights file is `gemma-4-12B-it-qat-UD-Q4_K_XL.gguf` (~6.3 GB) — there is no Q4_0 there; the repo also carries auxiliary GGUFs (`MTP/*`, `mmproj-*`) that must never be selected as the model. Alternates configurable: `unsloth/gemma-4-12b-it-GGUF` at Q4_K_M / Q5_K_M. Lighter fallback for weak hardware: Gemma 4 E4B (`unsloth/gemma-4-E4B-it-GGUF`, UD-Q4_K_XL, ~4.8 GB). Both are first-class presets: `llm.model: 12b|e4b` in config.yaml or `capella-podcast --model e4b <cmd>`; cached GGUFs are matched by model-name prefix AND quant so the two presets never collide in models/.
- LLM settings: thinking mode OFF by default (throughput). Sampling temperature 1.0, top_p 0.95, top_k 64. 256K context available.
- TTS: Kokoro-82M v1.0 via the `kokoro` package (`KPipeline`). Requires system `espeak-ng`; on Windows without admin rights, `msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\.tools\espeak-ng"` unpacks it locally and the app finds it there. First Kokoro use also fetches spacy `en_core_web_sm` (cached afterwards). MP3 encoding via the bundled libsndfile (soundfile package), no ffmpeg. Two distinct voices for the two-host podcast.
- DOCX: `python-docx`. Validate every generated file before moving on.
- Model provisioning: on first run, download the configured GGUF from Hugging Face into a configurable local cache, then load in-process. Gemma weights may be license-gated on HF (Unsloth mirror is often ungated); on a gated/missing download, print exact setup steps, do not fail obscurely.

## Course type detection

- `course.courseDesignModelType`: `GUIDED_PATH2` = Guided Path, `FLEX_PATH2` = FlexPath.
- Cross-check `course.flexPathAny` (false = GP, true = FPX). If the two disagree or the value is unknown, stop and report.
- Guided Path: modules are WEEKS (usually 10). FlexPath: modules are ASSESSMENTS (up to 10, often fewer).

## JSON parsing gotchas

- `units[]` order is the module order. Module number is 1-based index.
- Each `units[i]` has `unit.title`, `introductionId`, `activityIds[]`, `courseResourceReferenceIds[]`.
- Introductions: look up `introductionId` in `introductions[]` by `id`; `.text` is HTML.
- Activities: `activities[]` entries wrap an `activity` object (`.title`, `.code`, `.typeCodeFromCode`, `.activityType`, `.gradeType`, `.gradeWeight`, `.goal`) and carry `activityTextId`.
- Activity text: look up `activityTextId` in `activityText[]` by `id`; `.text` is HTML.
- Resources: spread across `resources[]`, `resourcesReferences[]`, `resourceFormats[]`. Pull readable titles and URLs (`resourceName`, `persistentLinks`, format `author`/`title`/`publisher`). Verified linkage: unit/activity `courseResourceReferenceIds` -> `resourcesReferences[].courseResourceReference.id`; each wrapper's `courseResourceIds`/`courseResourceFormatIds` -> `resources[].resource.id` / `resourceFormats[].id`. `resources[]` entries WRAP an inner `resource` object (wrapper key `courseMaterialsForamtIds` is misspelled in the real export). `persistentLinks` URLs arrive HTML-escaped (`&amp;`) — unescape them. Format `title`/`APACitation` contain HTML (`<em>`) — strip. FPX exports omit `gradeWeight`/`goal` keys on activities entirely.
- Every `.text` field is HTML: strip to plain text for LLM input, but preserve link URLs where they feed Recommended Resources.
- Be defensive: nulls, empty arrays, activities with no text, units with no introduction. Skip gracefully and note it in the manifest.
- SECOND INPUT FORMAT (flat course content export, first seen 2026-06, may arrive as `.txt`): top-level `syllabusContent`/`scoringGuideContent`/`unitContent`/`courseResources`, NO `course` object. `ingest()` sniffs the shape and dispatches (`_ingest_flat`). `unitContent` keys are `a<NN><Role>`: Overview = module intro; Summary/Instructions/resourceN/VendorN = activities; ScoringGuideLink = empty stub, skip. Assessment-structured, so always FPX. Course number/title regex-parsed from `syllabusContent.courseOverview` ("..., BHA-FPX3001 - Title"); module titles from `courseGrading.assessments`. `courseResources[*].activity` codes map to entries: `u<N>r<M>` -> `a<NN>resource<M>`, `u<N>v<M>` -> `a<NN>Vendor<M>`, `a<N>` -> `a<NN>Instructions`; entries also carry `resources: [{"resource_id_NNN": type}]`. Quirks: text is mojibake double-encoded ("youâ€™re") — repaired conservatively by `_fix_mojibake`; URLs are HTML-escaped (`&amp;`); relative `./Course_Files/...` hrefs are dropped (real download URLs come via `courseResources`); no per-activity grade weights.

## DOCX styling constants (match the example reports exactly)

- US Letter (12240 x 15840 DXA), 1 inch margins.
- Font Aptos, fallback Arial.
- Brand teal `0F4761` for the title and all section headings. Title 24pt. Body 12pt.
- Header: Capella logo top-left, `course.number` top-right. Logo file is dropped into `assets/` by the user; locate by case-insensitive `*capella*logo*.png`. Missing logo = warn and render without it, do not crash.
- Footer: page number with a thin horizontal rule above it.
- Lists use real Word list numbering, never literal bullet characters. Bold lead-in phrase, then normal text.
- Never emit the placeholder line "Remove or Replace: Header Is Not Doc Title".
- Section sets are data-driven per course type:
  - GP (`Weekly Summary: Week N`): Weekly Overview, Key Topics, Important Dates & Deadlines, Recommended Resources, Tips for Success.
  - FPX (`Assessment Summary: Assessment N`, configurable): Overview, Key Resource Topics, Recommended Resources, Ways to Connect, Tips for Success.

## Pipeline contract

1. Ingest -> `output/{course.number}/course-structure.json` (the intermediate; the contract between stages).
2. Summaries -> `summary.docx` per module (LLM writes from the intermediate).
3. Scripts -> `script.docx` per module (LLM writes from the summary DOCX, NOT raw JSON, so report edits flow through). Two-host conversational, ~3 to 6 min. Mark speaker turns machine-parseably (`HOST A:` / `HOST B:` prefixes).
4. Podcasts -> `podcast.mp3` per module (Kokoro reads the script, one voice per host, natural pauses).

## Regeneration rules

- `regen --from-summary --module N`: re-read the edited `summary.docx`, regenerate that module's script and mp3.
- `regen --from-script --module N`: re-read the edited `script.docx`, regenerate only that module's mp3.
- Always parse the edited DOCX back from disk. Never use cached text. Editing must not silently revert the user's wording.

## Output layout

```
output/{course.number}/
  course-structure.json
  manifest.json
  week-01/ (or assessment-01/)
    summary.docx
    script.docx
    podcast.mp3
```

## GUI

- Local web GUI at `src/capella_podcast/gui/`: stdlib-only `http.server` bound to 127.0.0.1 (no new dependencies), launched via `capella-podcast gui` / `python -m capella_podcast.gui` / `Start Podcast Generator.bat` at repo root. Serves a static single-page app (`gui/static/`) plus a JSON API.
- One background worker thread runs generation jobs sequentially (`gui/jobs.py`); stdout/stderr are teed into a log the page polls; cancellation is cooperative between modules. Pipeline calls mirror the cli.py loops (`gui/actions.py`) and never bypass manifest recording.
- Settings page edits config.yaml via targeted line replacement (`gui/config_edit.py`) so file comments are preserved; only whitelisted keys are editable, and the result is re-validated with `load_config` (reverted on failure).
- Staleness/edited detection compares artifact mtimes against manifest `generated_at` and against upstream files; the GUI surfaces regen actions accordingly.

## Repo conventions

- `CLAUDE.md` stays at repo root and is the spec of record.
- App code under `src/`. Config via a `config` file (model repo/quant/size, cache path, thinking toggle, sampling, host count, Kokoro voices/speed, output paths, brand color, logo override).
- Keep LLM prompts as code-managed templates, one per section type, easy to tune.
- Gitignore `models/` (GGUF cache) and `output/` (generated artifacts).
- Validate every generated DOCX; if invalid, fix and re-validate before continuing.
