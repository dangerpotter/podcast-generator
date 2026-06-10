"""Stage 4 engine: Kokoro-82M text-to-speech, fully local.

Kokoro needs the espeak-ng system library (used by its G2P fallback). We
detect it up front and fail with exact install instructions rather than a
deep stack trace. The Kokoro weights (~300 MB) download from Hugging Face on
first use into the standard HF cache; afterwards synthesis is offline.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np

from .config import AppConfig

SAMPLE_RATE = 24000
TURN_PAUSE_SECONDS = 0.45


class TTSDependencyError(Exception):
    pass


_WIN_ESPEAK_DLLS = (
    r"C:\Program Files\eSpeak NG\libespeak-ng.dll",
    r"C:\Program Files (x86)\eSpeak NG\libespeak-ng.dll",
)


def _win_dll_candidates() -> list[Path]:
    dlls = [Path(p) for p in _WIN_ESPEAK_DLLS]
    # Project-local unpacked copy (msiexec /a extract; no admin required).
    here = Path(__file__).resolve()
    for parent in (Path.cwd(), *here.parents):
        local = parent / ".tools" / "espeak-ng" / "eSpeak NG" / "libespeak-ng.dll"
        if local.is_file():
            dlls.insert(0, local)
    return dlls


def ensure_espeak() -> None:
    """Locate espeak-ng; on Windows also point phonemizer at the DLL."""
    if sys.platform == "win32":
        for dll in _win_dll_candidates():
            if dll.is_file():
                os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", str(dll))
                data = dll.parent / "espeak-ng-data"
                if data.is_dir():
                    os.environ.setdefault("ESPEAK_DATA_PATH", str(dll.parent))
                return
        if shutil.which("espeak-ng"):
            return
        raise TTSDependencyError(
            "espeak-ng is not installed (required by Kokoro TTS).\n"
            "Install it with:  winget install --id eSpeak-NG.eSpeak-NG\n"
            "or download the MSI from https://github.com/espeak-ng/espeak-ng/releases\n"
            "(no admin rights? extract it locally with:\n"
            '  msiexec /a espeak-ng.msi /qn TARGETDIR="<repo>\\.tools\\espeak-ng")\n'
            "then re-run this command."
        )
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return
    hint = (
        "brew install espeak-ng"
        if sys.platform == "darwin"
        else "sudo apt-get install espeak-ng   (or your distro's equivalent)"
    )
    raise TTSDependencyError(
        f"espeak-ng is not installed (required by Kokoro TTS).\nInstall it with:  {hint}"
    )


class KokoroTTS:
    """Lazy KPipeline wrapper; one pipeline reused for all turns."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._pipeline = None

    def load(self):
        if self._pipeline is not None:
            return self._pipeline
        ensure_espeak()
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise TTSDependencyError(
                "The kokoro package is not installed. Install with:\n"
                "  pip install kokoro>=0.9.2"
            ) from e
        # lang_code 'a' = American English (af_*/am_* voices).
        self._pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        return self._pipeline

    def voice_for(self, host: str, host_order: list[str]) -> str:
        voices = self.cfg.tts.voices or ["af_heart"]
        try:
            idx = host_order.index(host)
        except ValueError:
            idx = 0
        return voices[idx % len(voices)]

    def synthesize_turn(self, text: str, voice: str) -> np.ndarray:
        pipeline = self.load()
        chunks: list[np.ndarray] = []
        for result in pipeline(text, voice=voice, speed=self.cfg.tts.speed):
            audio = result.audio if hasattr(result, "audio") else result[2]
            if audio is None:
                continue
            arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            chunks.append(arr.astype(np.float32).reshape(-1))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def synthesize_conversation(self, turns: list[tuple[str, str]]) -> np.ndarray:
        host_order: list[str] = []
        for host, _ in turns:
            if host not in host_order:
                host_order.append(host)
        pause = np.zeros(int(TURN_PAUSE_SECONDS * SAMPLE_RATE), dtype=np.float32)
        pieces: list[np.ndarray] = []
        for i, (host, text) in enumerate(turns):
            audio = self.synthesize_turn(text, self.voice_for(host, host_order))
            if audio.size == 0:
                continue
            if pieces:
                pieces.append(pause)
            pieces.append(audio)
        if not pieces:
            raise RuntimeError("TTS produced no audio for any turn.")
        return np.concatenate(pieces)


def write_mp3(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write MP3 via libsndfile (bundled with the soundfile wheel, >=1.2)."""
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(str(path), audio, sample_rate, format="MP3")
    except Exception as e:
        wav_path = path.with_suffix(".wav")
        sf.write(str(wav_path), audio, sample_rate)
        raise RuntimeError(
            f"MP3 encoding failed ({e}); wrote WAV instead at {wav_path}. "
            f"Your libsndfile may lack MP3 support — upgrade the soundfile "
            f"package (pip install -U soundfile) and re-run."
        ) from e
