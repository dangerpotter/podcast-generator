# CLAUDE.md

Project memory for the Capella Course Podcast Generator. Read `docs/GOAL.md` for the full spec; this file is the durable, always-loaded summary. If anything here conflicts with `docs/GOAL.md`, `docs/GOAL.md` wins and this file should be updated.

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
- TTS: Kokoro-82M v1.0 via the `kokoro` package (`KPipeline`). Requires system `espeak-ng`; on Windows without admin rights, `msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\.tools\espeak-ng"` unpacks it locally and the app finds it there. First Kokoro use also auto-downloads spacy `en_core_web_sm` (one-time, ~12 MB, cached in the HF cache afterwards). MP3 encoding via the bundled libsndfile (soundfile package), no ffmpeg. Two distinct voices for the two-host podcast.
- DOCX: `python-docx`. Validate every generated file before moving on.
- Model provisioning: on first run, download the configured GGUF from Hugging Face into a configurable local cache, then load in-process. Gemma weights may be license-gated on HF (Unsloth mirror is often ungated); on a gated/missing download, print exact setup steps, do not fail obscurely.

## Course type detection

- `course.courseDesignModelType`: `GUIDED_PATH2` = Guided Path, `FLEX_PATH2` = FlexPath.
- Cross-check `course.flexPathAny` (false = GP, true = FPX). If the two disagree or the value is unknown, stop and report.
- Guided Path: modules are WEEKS (usually 10). FlexPath: modules are ASSESSMENTS (up to 10, often fewer).

## Course Compass export format

A second ingest format is auto-detected by `ingest._detect_format()`: if the root of the JSON has both `syllabusContent` and `unitContent` keys, it is a Course Compass export and is handled by `_ingest_compass_format()`. Both formats produce the same intermediate `course-structure.json` schema.

Key parsing differences from the standard export:
- Course number/name extracted by regex from `syllabusContent.courseOverview.openingLanguage`.
- Course type inferred from the course number ("FPX" in the number → FlexPath); use `force_course_type="GP"|"FPX"` to override.
- Modules built from `syllabusContent.courseGrading.assessments[]` in order.
- Per-module content keyed under `unitContent` as `a{NN}Overview`, `a{NN}Instructions`, `a{NN}Summary`, `a{NN}resource1`…
- Scoring criteria from `scoringGuideContent.a{NN}ScoringGuide.table.criteria[]`.
- `gradeWeight` and `goal` are always `null` in Compass exports.

## JSON parsing gotchas

- `units[]` order is the module order. Module number is 1-based index.
- Each `units[i]` has `unit.title`, `introductionId`, `activityIds[]`, `courseResourceReferenceIds[]`.
- Introductions: look up `introductionId` in `introductions[]` by `id`; `.text` is HTML.
- Activities: `activities[]` entries wrap an `activity` object (`.title`, `.code`, `.typeCodeFromCode`, `.activityType`, `.gradeType`, `.gradeWeight`, `.goal`) and carry `activityTextId`.
- Activity text: look up `activityTextId` in `activityText[]` by `id`; `.text` is HTML.
- Resources: spread across `resources[]`, `resourcesReferences[]`, `resourceFormats[]`. Pull readable titles and URLs (`resourceName`, `persistentLinks`, format `author`/`title`/`publisher`). Verified linkage: unit/activity `courseResourceReferenceIds` -> `resourcesReferences[].courseResourceReference.id`; each wrapper's `courseResourceIds`/`courseResourceFormatIds` -> `resources[].resource.id` / `resourceFormats[].id`. `resources[]` entries WRAP an inner `resource` object (wrapper key `courseMaterialsForamtIds` is misspelled in the real export). `persistentLinks` URLs arrive HTML-escaped (`&amp;`) — unescape them. Format `title`/`APACitation` contain HTML (`<em>`) — strip. FPX exports omit `gradeWeight`/`goal` keys on activities entirely.
- Every `.text` field is HTML: strip to plain text for LLM input, but preserve link URLs where they feed Recommended Resources.
- Be defensive: nulls, empty arrays, activities with no text, units with no introduction. Skip gracefully and note it in the manifest.

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
2. Summaries -> `cc_{lowercase_course_id}_assessment_summary-{NN}.docx` per module (LLM writes from the intermediate).
3. Scripts -> `cc_{lowercase_course_id}_podcast_script-{NN}.docx` per module (LLM writes from the summary DOCX, NOT raw JSON, so report edits flow through). Two-host conversational, ~3 to 6 min. Mark speaker turns machine-parseably (`HOST A:` / `HOST B:` prefixes).
4. Podcasts -> `cc_{lowercase_course_id}_podcast_overview-{NN}.mp3` per module (Kokoro reads the script, one voice per host, natural pauses).

`NN` is the two-digit module/assessment number. Example for Assessment 1 of
`MBA-FPX5006`: `cc_mba-fpx5006_assessment_summary-01.docx`,
`cc_mba-fpx5006_podcast_script-01.docx`, and
`cc_mba-fpx5006_podcast_overview-01.mp3`.
The `assessment_summary` filename label applies to both Guided Path week
modules and FlexPath assessments.

## Regeneration rules

- `regen --from-summary --module N`: re-read the edited assessment summary DOCX, regenerate that module's script and mp3.
- `regen --from-script --module N`: re-read the edited podcast script DOCX, regenerate only that module's mp3.
- Always parse the edited DOCX back from disk. Never use cached text. Editing must not silently revert the user's wording.

## Output layout

```
output/{course.number}/
  course-structure.json
  manifest.json
  week-01/ (or assessment-01/)
    cc_{lowercase_course_id}_assessment_summary-{NN}.docx
    cc_{lowercase_course_id}_podcast_script-{NN}.docx
    cc_{lowercase_course_id}_podcast_overview-{NN}.mp3
```

## GUI

- Local web GUI at `src/capella_podcast/gui/`: stdlib-only `http.server` bound to 127.0.0.1 (no new dependencies), launched via `capella-podcast gui` / `python -m capella_podcast.gui` / `Start Podcast Generator.bat` at repo root. Serves a static single-page app (`gui/static/`) plus a JSON API.
- One background worker thread runs generation jobs sequentially (`gui/jobs.py`); stdout/stderr are teed into a log the page polls; cancellation is cooperative between modules. Pipeline calls mirror the cli.py loops (`gui/actions.py`) and never bypass manifest recording.
- Settings page edits config.yaml via targeted line replacement (`gui/config_edit.py`) so file comments are preserved; only whitelisted keys are editable, and the result is re-validated with `load_config` (reverted on failure).
- Staleness/edited detection compares artifact mtimes against manifest `generated_at` and against upstream files; the GUI surfaces regen actions accordingly.

## Repo conventions

- `CLAUDE.md` stays at repo root. Full spec lives in `docs/GOAL.md`.
- App code under `src/`. Config via a `config` file (model repo/quant/size, cache path, thinking toggle, sampling, host count, Kokoro voices/speed, output paths, brand color, logo override).
- Keep LLM prompts as code-managed templates, one per section type, easy to tune.
- Gitignore `models/` (GGUF cache) and `output/` (generated artifacts).
- Validate every generated DOCX; if invalid, fix and re-validate before continuing.
