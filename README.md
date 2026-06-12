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

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10/11, macOS, or Linux | Windows: double-click the `.bat` launcher |
| Python 3.10 – 3.12 | 3.12 recommended; keep the **py launcher** option checked on Windows |
| ~15 GB free disk | ~6.3 GB for the 12B model GGUF + ~1 GB for Kokoro weights + working space |
| ~12 GB free RAM | 12B model default; use `--model e4b` (~4 GB GGUF) on 8 GB machines |
| Internet for first run | Model GGUFs download from Hugging Face once, then cached |
| **espeak-ng** (system library) | Required only for podcast (MP3) generation; summaries and scripts work without it |

## Setup

### Windows — double-click launcher (recommended)

Double-click **`Start Podcast Generator.bat`**. On first run it:

1. Creates a Python virtual environment (`.venv/`).
2. Downloads and installs `llama-cpp-python` using a prebuilt binary wheel — no
   C++ compiler required on most machines.
3. Installs the rest of the app dependencies.
4. Checks for `espeak-ng` and offers to install it via `winget` if missing.

After setup it opens the GUI automatically.

### Manual setup (any platform)

```powershell
py -3.12 -m venv .venv            # any Python 3.10+
.venv\Scripts\python -m pip install --upgrade pip

# Install llama-cpp-python with a prebuilt binary wheel first.
# Without this step, pip may try to compile it from C++ source, which
# needs Visual Studio Build Tools (Windows) or Xcode / build-essential.
.venv\Scripts\pip install "llama-cpp-python>=0.3.0" `
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# CUDA users: pick a wheel from https://github.com/abetlen/llama-cpp-python/releases
# Metal (Apple Silicon): the standard wheel auto-detects Metal; no special index needed.

.venv\Scripts\pip install -e .
```

**espeak-ng** (required by Kokoro for podcast generation):

- Windows: `winget install --id eSpeak-NG.eSpeak-NG`, or without admin rights:
  `msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\.tools\espeak-ng"` (auto-detected there)
- macOS: `brew install espeak-ng`
- Linux: `sudo apt-get install espeak-ng`

Then drop into the repo:

1. Your Capella course JSON export (anywhere; you pass the path to `ingest`).
2. The Capella logo PNG into `assets/` (matched case-insensitively as
   `*capella*logo*.png`; header renders without it if missing).

Settings live in [config.yaml](config.yaml).

### Verify the installation

```powershell
capella-podcast doctor
```

This checks Python, `llama-cpp-python`, `kokoro`, `espeak-ng`, available RAM, and
the model cache, then prints a clear pass/warn/fail for each item.

## Usage

### GUI

Double-click **`Start Podcast Generator.bat`** (or run `capella-podcast gui`).
A local web app opens at `http://127.0.0.1:8765/` — local only, nothing is
served beyond your machine. From there you can ingest a course JSON (native
file picker), generate or regenerate any stage per module or for the whole
course, watch live progress logs, play podcasts in the browser, open the DOCX
files in Word, and edit the common settings (model preset, voices, podcast
length). Stale artifacts are flagged: if you edit an assessment summary DOCX in Word,
the GUI shows an "edited" tag and offers one-click regeneration of the
downstream script and podcast.

`capella-podcast gui --port N --no-browser` for variations.

### CLI

```powershell
capella-podcast ingest <course.json>     # Stage 1: intermediate structure
capella-podcast summaries [--module N]   # Stage 2: Summary Report DOCX
capella-podcast scripts   [--module N]   # Stage 3: Podcast Script DOCX
capella-podcast podcasts  [--module N]   # Stage 4: Podcast MP3
capella-podcast regen --from-summary --module N   # edited report -> script + mp3
capella-podcast regen --from-script  --module N   # edited script -> mp3
capella-podcast run-all <course.json>    # full pipeline
capella-podcast doctor                   # check all dependencies
```

Artifacts land in `output/{course.number}/` (`course-structure.json`,
`manifest.json`, and one `week-NN/` or `assessment-NN/` folder per module).
Generated filenames use the lowercase course ID and two-digit module number:
`cc_{courseID}_assessment_summary-NN.docx`,
`cc_{courseID}_podcast_script-NN.docx`, and
`cc_{courseID}_podcast_overview-NN.mp3`.

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

Edit any generated assessment summary DOCX and run `regen --from-summary --module N` to
rebuild that module's script and podcast from your edited report. Edit a
podcast script DOCX and run `regen --from-script --module N` to rebuild only the MP3.
Edited files are always re-parsed from disk; your wording is never reverted.

## Troubleshooting

**`llama-cpp-python` install fails / won't compile**

The package needs a prebuilt binary wheel or a C++ compiler. Try:

```powershell
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

If that fails, install [VS Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe)
(free; choose "Desktop development with C++"), then retry `pip install -e .`.
NVIDIA GPU users: download a CUDA wheel from the
[llama-cpp-python releases page](https://github.com/abetlen/llama-cpp-python/releases).

**`espeak-ng` not found**

Only required for podcast (MP3) generation. Summaries and scripts work without it.
Windows: `winget install --id eSpeak-NG.eSpeak-NG`. No admin rights:
`msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\.tools\espeak-ng"` — the app
detects it there automatically.

**Model download fails ("gated repo")**

The Unsloth mirrors used by default are ungated. If you override `llm.repo_id`
to a Google-hosted repo, you may need to accept the Gemma license on Hugging
Face and run `huggingface-cli login`. Alternatively, copy the GGUF manually into
`models/` and set `llm.model_file` in `config.yaml`.

**Out-of-memory crash during generation**

Switch to the lighter model: `capella-podcast --model e4b run-all <course.json>`,
or set `llm.model: e4b` in `config.yaml`. Also try closing other applications to
free RAM before running.

**MP3 output is a WAV instead**

Your `libsndfile` version may not include MP3 support. Upgrade:
`pip install -U soundfile`. The WAV file is fully playable in the meantime.

**Run `capella-podcast doctor` for a full dependency health check.**

## Repo layout

- `src/capella_podcast/` — the package (ingest, LLM, DOCX render/validate, TTS, CLI)
- `src/capella_podcast/gui/` — local web GUI (stdlib HTTP server + static page,
  no extra dependencies); launched by `capella-podcast gui` or the root `.bat`
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
