"""Stage 4: Podcast MP3, one per module.

Reads the script DOCX back from disk (so user edits flow through), maps each
host to a distinct Kokoro voice, inserts natural pauses between turns, and
writes one MP3 per module.
"""

from __future__ import annotations

from pathlib import Path

from .config import AppConfig
from .docx_reader import read_script_turns
from .ingest import module_dir_name
from .tts import SAMPLE_RATE, KokoroTTS, write_mp3


def generate_module_podcast(
    cfg: AppConfig,
    tts: KokoroTTS,
    structure: dict,
    module: dict,
    course_dir: Path,
) -> Path:
    n = module["number"]
    mod_dir = course_dir / module_dir_name(structure, n)
    script_path = mod_dir / "script.docx"
    if not script_path.is_file():
        raise FileNotFoundError(
            f"{script_path} not found. Generate scripts first "
            f"(`capella-podcast scripts --module {n}`)."
        )
    turns = read_script_turns(script_path)
    if len(turns) < 2:
        raise RuntimeError(
            f"{script_path} has no parseable HOST A:/HOST B: speaker turns; "
            f"cannot synthesize audio."
        )
    audio = tts.synthesize_conversation(turns)
    out = mod_dir / "podcast.mp3"
    write_mp3(out, audio, SAMPLE_RATE)

    # Validate: the file must decode and have a sane duration.
    import soundfile as sf

    info = sf.info(str(out))
    duration = info.frames / info.samplerate
    if duration < 30:
        raise RuntimeError(f"{out} is suspiciously short ({duration:.0f}s).")
    print(f"  validated OK: {out} ({duration/60:.1f} min, {info.samplerate} Hz)")
    return out
