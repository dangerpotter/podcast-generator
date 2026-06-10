# Capella Course Podcast Generator

Fully local, offline pipeline. Ingests a Capella course content JSON export and,
for every module, produces a Summary Report (DOCX), a Podcast Script (DOCX)
derived from that report, and a Podcast (MP3) derived from that script, with an
edit-and-regenerate loop.

- **LLM:** Gemma 4 (12B QAT by default, E4B fallback) embedded in-process via
  `llama-cpp-python` — no Ollama, no daemon, no cloud, no API keys.
- **TTS:** Kokoro-82M via the `kokoro` package, two distinct host voices.
- **Offline:** after the first model download, a full run makes zero network calls.
- **Grounded:** grade weights, deadlines, and resource names/links are rendered
  deterministically from the export data — the LLM only summarizes and organizes,
  and is never allowed to invent dates, weights, or resources.

Spec lives in [CLAUDE.md](CLAUDE.md) (summary) and [docs/GOAL.md](docs/GOAL.md)
(authoritative).

## Setup

```powershell
py -3.12 -m venv .venv            # any Python 3.10+
.venv\Scripts\python -m pip install -e .
```

System dependency: **espeak-ng** (required by Kokoro).

- Windows: `winget install --id eSpeak-NG.eSpeak-NG`, or without admin rights:
  `msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\.tools\espeak-ng"` (auto-detected there)
- macOS: `brew install espeak-ng` — Linux: `sudo apt-get install espeak-ng`

Then drop into the working folder:

1. your Capella course JSON export (anywhere; you pass the path to `ingest`),
2. the Capella logo PNG into `assets/` (matched case-insensitively as
   `*capella*logo*.png`; omitted from this repo — the header renders without it
   if missing).

Settings live in [config.yaml](config.yaml).

## Usage

```powershell
capella-podcast ingest <course.json>     # Stage 1: intermediate structure
capella-podcast summaries [--module N]   # Stage 2: Summary Report DOCX
capella-podcast scripts   [--module N]   # Stage 3: Podcast Script DOCX
capella-podcast podcasts  [--module N]   # Stage 4: Podcast MP3
capella-podcast regen --from-summary --module N   # edited report -> script + mp3
capella-podcast regen --from-script  --module N   # edited script -> mp3
capella-podcast run-all <course.json>    # full pipeline
```

Artifacts land in `output/{course.number}/` (`course-structure.json`,
`manifest.json`, and one `week-NN/` or `assessment-NN/` folder per module with
`summary.docx`, `script.docx`, `podcast.mp3`).

### Choosing the model

Two presets are built in; pick per run with `--model` or persistently via
`llm.model` in [config.yaml](config.yaml):

| Preset | Weights | Size | Notes |
|--------|---------|------|-------|
| `12b` (default) | `unsloth/gemma-4-12B-it-qat-GGUF` (UD-Q4_K_XL) | ~6.3 GB | Best quality; wants ~12 GB free RAM; slow on weak CPUs |
| `e4b` | `unsloth/gemma-4-E4B-it-GGUF` (UD-Q4_K_XL) | ~4 GB | Much faster on CPU-only machines |

```powershell
capella-podcast --model e4b run-all <course.json>
```

The first run with a preset downloads its GGUF into `models/`; both presets can
coexist in the cache. Advanced: pin any repo/quant/file via the commented
overrides in config.yaml.

### Edit-and-regenerate loop

Edit any generated `summary.docx` and run `regen --from-summary --module N` to
rebuild that module's script and podcast from your edited report. Edit a
`script.docx` and run `regen --from-script --module N` to rebuild only the MP3.
Edited files are always re-parsed from disk; your wording is never reverted.

## Repo layout

- `src/capella_podcast/` — the package (ingest, LLM, DOCX render/validate, TTS, CLI)
- `docs/GOAL.md` — full specification
- `tests/` — validation scripts (ingest checks, layout fingerprint vs the
  reference reports, end-to-end tree validation, regen acceptance test)
- `samples/`, `assets/` — empty in this repo: Capella course exports, example
  reports, and the Capella logo are proprietary and not redistributed; supply
  your own
- `models/`, `output/` — created at runtime (gitignored)

## License

MIT — see [LICENSE](LICENSE). Gemma weights are subject to Google's Gemma
license terms; Kokoro-82M is Apache-2.0. Capella course content and branding
belong to Capella University and are not included.
