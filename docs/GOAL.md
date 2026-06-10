# GOAL: Local Course Podcast Generator (Capella FlexPath + Guided Path)

## Objective

Build a fully local, offline app that ingests a Capella course content JSON export and, for every module in the course, produces three artifacts: a Summary Report (DOCX), a Podcast Script (DOCX) derived from that report, and a Podcast (MP3) derived from that script. The app must support an edit-and-regenerate loop so a human can revise the report or the script and re-run the downstream steps.

No external/paid APIs. Text generation and text-to-speech both run on-device. Treat "runs on a normal work laptop" as a hard design constraint.

## Hard constraints

- Local only. No cloud LLM or TTS calls. No API keys.
- Text-to-speech: Kokoro-82M (v1.0), via the `kokoro` Python package (`pip install kokoro>=0.9.2`, `KPipeline`). Requires `espeak-ng` as a system dependency; detect it and fail with a clear install message if missing. Apache-2.0, ~300MB, runs on CPU, English (US + British) voices.
- Text generation (summary + script): Gemma 4 family (Apache-2.0), run locally through an embedded llama.cpp. Default model: Gemma 4 12B Unified, instruction-tuned, QAT GGUF from `unsloth/gemma-4-12B-it-qat-GGUF` (Q4_0). QAT (quantization-aware training) holds near-bf16 quality at 4-bit and is reported to keep long-context summarization stable, which is exactly this workload. Acceptable alternates (make repo and quant configurable): the standard `unsloth/gemma-4-12b-it-GGUF` at Q4_K_M or Q5_K_M. Gemma 4 12B has a 256K context window and native `system` role support. Keep the model's thinking mode OFF by default for throughput (this is straightforward summarization and we generate many modules); expose it as a config toggle.
- LLM runtime: embed llama.cpp directly in the app via `llama-cpp-python` (load the GGUF in-process, no separate daemon to install or manage). Do NOT require Ollama. The app must provision and run the model itself so it "just works": on first run, download the GGUF from Hugging Face (via `huggingface_hub`) into a configurable local cache, verify it, then load it in-process. Prefer prebuilt `llama-cpp-python` wheels; auto-detect Metal/CUDA acceleration when present and fall back to CPU. A bundled `llama-server` subprocess over localhost is an acceptable alternative only if in-process binding proves problematic; document whichever is used.
- Lighter fallback model for weak hardware: Gemma 4 E4B GGUF (~4.5B effective, 128K context, much smaller and faster on CPU). The default 12B Q4 GGUF is ~7-8GB on disk and wants roughly 10-12GB free RAM with context; it is comfortable on Apple Silicon or any GPU but slow on CPU-only 8GB machines. Detect available RAM at startup and warn (or auto-suggest E4B) when below a sane threshold.
- The app runs against a blank working folder. A Capella logo PNG will be dropped into that folder by the user; locate it by filename pattern (e.g. `*capella*logo*.png`, case-insensitive) and place it in the DOCX header. If no logo is found, render the header without it and warn, do not crash.

## Inputs

1. One course content JSON file (the Capella curriculum export). Two real samples are provided: a Guided Path course and a FlexPath course. Inspect both before writing the parser.
2. A Capella logo PNG in the working folder.
3. Two example Summary Report DOCX files (one GP, one FPX). These are LAYOUT/STYLE references only. They do not correspond 1:1 to the sample JSONs, and they contain a placeholder line "Remove or Replace: Header Is Not Doc Title" that must NOT appear in generated output.

## Course model detection

Detect FlexPath vs Guided Path from the JSON, do not ask the user:

- `course.courseDesignModelType`: `"GUIDED_PATH2"` for Guided Path, `"FLEX_PATH2"` for FlexPath.
- Cross-check with `course.flexPathAny` (`true` for FlexPath, `false` for GP).
- Guided Path: instructor-led, modules are WEEKS, typically 10 units, `unit.duration` is 1.
- FlexPath: self-paced, modules are ASSESSMENTS, up to 10 units but often fewer (sample has 4), `unit.duration` is 0.

If the two fields disagree or the value is unrecognized, stop and report it clearly rather than guessing.

## JSON parsing map (verify against both sample files; handle null/missing gracefully)

Course metadata:
- `course.name`, `course.number`, `course.credits`, `course.courseDesignModelType`, `course.flexPathAny`.

Modules (one per entry in `units[]`, preserve array order, 1-based index = module number):
- `units[i].unit.title`, `units[i].unit.duration`
- `units[i].introductionId` -> look up in `introductions[]` by `id`, field `.text` is HTML.
- `units[i].activityIds[]` -> look up in `activities[]` by `activity.id`.
- `units[i].courseResourceReferenceIds[]` -> look up in `resourcesReferences[]`.

Activities:
- Each `activities[]` entry has an `activity` object plus an `activityTextId`.
- Useful activity fields: `activity.title`, `activity.code` (e.g. `u01a1`, `u01s1`, `u01d1`), `activity.typeCodeFromCode`, `activity.activityType` (Study, Discussion, Assessment, etc.), `activity.gradeType` (e.g. `UNGRADED_REQUIRED`, `PARTICIPATION`, graded), `activity.gradeWeight`, `activity.goal`.
- `activityTextId` -> look up in `activityText[]` by `id`, field `.text` is HTML (contains reading lists, resource links, instructions).

Resources (for the Recommended Resources section):
- `resourcesReferences[]`, `resources[]`, `resourceFormats[]`. Resource entries carry `resourceName`, `persistentLinks`, `type`, and format entries carry `author`, `title`, `publisher`. Extract human-readable titles and any URLs; preserve hyperlink targets in the DOCX.

Optional grade context (use only if it improves the Important Dates / grade-weight content):
- `competencies[]`, `criteria[]`, `performanceLevels[]`.

All `.text` fields are HTML. Strip tags to plain text for LLM input, but preserve link URLs where they feed the Recommended Resources list.

## Pipeline

Stage 1 - Ingest and structure
- Load JSON, detect course model, build an in-memory (and on-disk JSON) intermediate structure: course meta plus an ordered list of modules, each carrying its title, overview source text, activities (with grade weights and due signals), and resource links. This intermediate is the contract between stages and makes regeneration cheap.

Stage 2 - Summary Report (DOCX), one per module
- The LLM (Gemma 4) writes the prose sections from the structured module data. It must not invent facts not present in the source; it summarizes and organizes.
- Guided Path report sections (match the GP example layout):
  - Title: `Weekly Summary: Week {N}`
  - Subtitle: `{course.number} - {course.name}`
  - `Weekly Overview` (prose, from the unit introduction)
  - `Key Topics` (bulleted, bold lead-in per topic, drawn from study activities and intro)
  - `Important Dates & Deadlines` (bulleted, name the graded activities with their grade weight and that they are due that week)
  - `Recommended Resources` (bulleted, readings/media with working hyperlinks)
  - `Tips for Success` (bulleted, actionable)
- FlexPath report sections (match the FPX example layout):
  - Title: `Assessment Summary: Assessment {N}` (see Open Questions, make this configurable)
  - Subtitle: `{course.number} - {course.name}`
  - `Overview`
  - `Key Resource Topics`
  - `Recommended Resources`
  - `Ways to Connect`
  - `Tips for Success`
- DOCX styling must match the provided examples:
  - US Letter (8.5 x 11), 1 inch margins.
  - Font Aptos (fallback Arial if Aptos unavailable on the host).
  - Title 24pt, section headings bold, body 12pt.
  - Brand color for title and section headings: hex `0F4761` (deep teal, sampled from the example files).
  - Header: Capella logo top-left, `course.number` top-right.
  - Footer: page number, with a thin horizontal rule above it.
  - Bulleted lists use real Word list numbering, never literal bullet characters. Bold lead-in phrase then normal text.
  - Do not emit the "Remove or Replace" placeholder.

Stage 3 - Podcast Script (DOCX), one per module
- Generated by the LLM from the Summary Report (not from raw JSON), so edits to the report flow through.
- Format: two-host conversational podcast (NotebookLM style), warm and plain-spoken, about 3 to 6 minutes of speech per module. Host count is configurable but defaults to 2.
- The script DOCX must clearly and machine-parseably mark speaker turns (for example a consistent `HOST A:` / `HOST B:` prefix per paragraph) so Stage 4 can map turns to voices. Keep stage directions out of the spoken lines.

Stage 4 - Podcast (MP3), one per module
- Kokoro-82M reads the script. Parse speaker turns and assign a distinct Kokoro voice per host (configurable voice IDs and speaking rate). Concatenate turns into a single MP3 per module with natural pauses.
- Output MP3, 24kHz or better, reasonable bitrate.

## Edit and regenerate loop (core requirement)

- After Stage 2, the user can edit the Summary Report DOCX, then re-run to regenerate that module's script and podcast from the edited report.
- After Stage 3, the user can edit the Script DOCX, then re-run to regenerate only the podcast.
- Regeneration must be targetable per module and per stage, and must read back the edited DOCX content (parse the DOCX, do not rely on cached text). Editing a report should not silently revert the user's wording.

## Suggested interface

- CLI is sufficient for the MVP. Subcommands along the lines of:
  - `ingest <course.json>` -> builds the intermediate structure.
  - `summaries [--module N|all]` -> Stage 2.
  - `scripts [--module N|all]` -> Stage 3.
  - `podcasts [--module N|all]` -> Stage 4.
  - `regen --from-summary --module N` and `regen --from-script --module N`.
  - `run-all <course.json>` -> full pipeline.
- A `config` file (YAML or JSON) for: LLM model repo + quant + size (default `unsloth/gemma-4-12B-it-qat-GGUF`, Q4_0), model cache path, thinking-mode toggle, sampling params (Gemma 4 defaults: temperature 1.0, top_p 0.95, top_k 64), host count, Kokoro voice IDs and speed, output paths, brand color, logo path override.
- LLM runtime is embedded llama.cpp via `llama-cpp-python`, loaded in-process. On first run, auto-download the configured GGUF from Hugging Face into the cache; verify and reuse on later runs. Note: Google's Gemma weights are typically license-gated on Hugging Face, so first-run download may require an HF token / accepting the Gemma license; the Unsloth mirror is often ungated. Detect a missing model, missing wheel, or gated-download failure and print exact setup commands rather than failing obscurely.

## Output layout

```
/output/{course.number}/
  course-structure.json          # the intermediate from Stage 1
  manifest.json                  # what was generated, when, from which source
  week-01/  (or assessment-01/)
    summary.docx
    script.docx
    podcast.mp3
  week-02/
    ...
```

## Acceptance criteria

- Given the provided GP sample JSON, generates 10 modules, each with a Summary Report whose layout matches the GP example (header logo + course code, teal title/headings, the five GP sections, working resource links, no placeholder text).
- Given the provided FPX sample JSON, correctly detects FlexPath, generates the right number of modules (4 for the sample) with the FPX section set.
- Each module yields a valid DOCX report, a valid DOCX script with parseable speaker turns, and a playable MP3.
- Editing a Summary Report DOCX and running `regen --from-summary` regenerates that module's script and MP3 reflecting the edit.
- Editing a Script DOCX and running `regen --from-script` regenerates only that module's MP3.
- After the model GGUF is cached, an entire run completes with no network calls (verify). Inference runs on CPU when no accelerator is present (slow on weak hardware, acceptable), and uses Metal/CUDA automatically when available.
- Clear errors for: missing espeak-ng, missing `llama-cpp-python` wheel, missing or gated model download, unrecognized course model type, missing logo (warn only, do not crash).

## Engineering notes

- Validate every generated DOCX after creation; if invalid, fix and re-validate before moving on.
- Be defensive about the JSON: nulls, empty arrays, activities with no text, units with no introduction. Skip gracefully and note it in the manifest.
- Keep the LLM prompts as code-managed templates, one per section type, so they are easy to tune. The LLM summarizes and reorganizes only; it must not fabricate dates, grade weights, or resources.
- Make the GP-vs-FPX section sets data-driven (a small template definition per model type) rather than hard-coded branching scattered through the code.

## Open questions to resolve (flagged for the requester, do not block the build, pick the documented default and note the assumption)

1. FPX titling. The written spec says FlexPath should be an "Assessment Summary Report" with modules as assessments, but the FPX example DOCX is titled "Weekly Summary: Week 3" while its body references "Assessment 2." Default chosen: follow the written spec (`Assessment Summary: Assessment {N}`), keep the label fully configurable, and confirm with the requester which wording they actually want.
2. Podcast length. Two-host conversational is confirmed. Default target 3 to 6 minutes of speech per module; confirm the preferred length.
3. Kokoro voice selection per host (US vs British, male/female). Default to two distinct US English voices, configurable.
4. Target hardware floor. Default model is Gemma 4 12B QAT GGUF (Q4_0), which needs roughly 10-12GB free RAM and is slow on CPU-only machines. Confirm the weakest machine this must run on; if 8GB CPU-only laptops are in scope, set the default to Gemma 4 E4B instead.
